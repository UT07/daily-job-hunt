"""AI helper for Lambda functions — real council with diverse generators and numeric critic scoring."""
import hashlib
import json
import logging
import random
import re
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


# ---------------------------------------------------------------------------
# Provider pool
# ---------------------------------------------------------------------------

def _build_provider_list() -> list[dict]:
    """Build the full provider config list with all available models."""
    openrouter_headers = {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}
    openrouter_key = "/naukribaba/OPENROUTER_API_KEY"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_models = [
        "qwen/qwen3.6-plus:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "z-ai/glm-4.5-air:free",
        "google/gemma-3-27b-it:free",
    ]

    providers = [
        {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "llama-3.3-70b-versatile",
         "timeout": 60},
        {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
         "key_param": "/naukribaba/NVIDIA_API_KEY", "model": "meta/llama-3.3-70b-instruct",
         "timeout": 120},
    ]
    for m in openrouter_models:
        providers.append({
            "name": f"openrouter/{m.split('/')[-1].split(':')[0]}",
            "url": openrouter_url, "key_param": openrouter_key, "model": m,
            "timeout": 90, "extra_headers": openrouter_headers,
        })
    providers.append(
        {"name": "qwen", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
         "key_param": "/naukribaba/QWEN_API_KEY", "model": "qwen-plus",
         "timeout": 90},
    )
    return providers


def _model_family(model: str) -> str:
    """Collapse model names to canonical family for dedup.

    Ensures council never picks two instances of the same underlying model
    (e.g. llama-3.3-70b from both Groq and NVIDIA).
    """
    m = model.lower().split("/")[-1]
    m = m.replace(":free", "")
    for prefix in ("deepseek", "llama-3.3", "llama-3.1", "llama-4", "qwen3",
                    "qwen-plus", "qwen-turbo", "qwen-max",
                    "mistral-small", "nemotron", "hermes", "gemma", "glm", "step"):
        if m.startswith(prefix):
            return prefix
    return m


def _select_diverse_providers(
    providers: list[dict],
    n: int,
    exclude_families: set[str] | None = None,
) -> list[dict]:
    """Pick N providers from distinct model families.

    Shuffles before selection so different runs get different subsets.
    Excludes any families in exclude_families (used to pick critics that
    didn't generate).
    """
    exclude_families = exclude_families or set()

    shuffled = list(providers)
    random.shuffle(shuffled)

    seen: set[str] = set()
    result: list[dict] = []
    for p in shuffled:
        fam = _model_family(p["model"])
        if fam in seen or fam in exclude_families:
            continue
        seen.add(fam)
        result.append(p)
        if len(result) >= n:
            break
    return result


# ---------------------------------------------------------------------------
# Single-provider call
# ---------------------------------------------------------------------------

def _call_provider(
    provider: dict,
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | None:
    """Make a single AI call to one provider. Returns dict or None on failure."""
    try:
        api_key = get_param(provider["key_param"])
        if not api_key or api_key == "mock-value":
            return None

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if "extra_headers" in provider:
            headers.update(provider["extra_headers"])

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = httpx.post(
            provider["url"],
            headers=headers,
            json={"model": provider["model"], "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
            timeout=provider.get("timeout", 60),
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"].get("content")
            if not content:
                logger.warning(f"[ai] {provider['name']} returned empty content")
                return None
            return {"content": content, "provider": provider["name"], "model": provider["model"]}
        elif resp.status_code == 429:
            logger.warning(f"[ai] {provider['name']} rate limited")
        else:
            logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"[ai] {provider['name']} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# ai_complete — single call with failover
# ---------------------------------------------------------------------------

def ai_complete(prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.3) -> dict:
    """Call AI provider with failover chain. Tries each provider once."""
    providers = _build_provider_list()

    # A/B testing: 20% of calls shuffle tail providers
    if random.random() < 0.2:
        tail = providers[1:]
        random.shuffle(tail)
        providers = [providers[0]] + tail
        logger.info(f"[ab_test] shuffled: {[p['name'] for p in providers[:3]]}...")

    last_error = None
    for provider in providers:
        result = _call_provider(provider, prompt, system, temperature, max_tokens)
        if result:
            logger.info(f"[ai] {result['provider']}/{result['model']} succeeded")
            return result
        last_error = f"{provider['name']} failed"

    raise RuntimeError(f"All {len(providers)} AI providers failed. Last: {last_error}")


# ---------------------------------------------------------------------------
# Critic score parsing
# ---------------------------------------------------------------------------

def _parse_critic_scores(raw: str, expected_count: int) -> list[int] | None:
    """Extract a JSON array of integer scores from a critic's response."""
    text = raw.strip()
    # Strip markdown fences
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Find JSON array
    match = re.search(r"\[[\d\s,]+\]", text)
    if not match:
        return None

    try:
        scores = json.loads(match.group())
        if len(scores) != expected_count:
            return None
        return [max(0, min(100, int(s))) for s in scores]
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# council_complete — diverse generators + numeric critic scoring
# ---------------------------------------------------------------------------

def council_complete(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
) -> dict:
    """Generate multiple AI responses from diverse models, score with numeric critic.

    1. Pick n_generators providers from DISTINCT model families
    2. Generate candidates independently
    3. Pick 1 critic from a DIFFERENT family than any generator
    4. Critic scores each candidate 0-100 on accuracy, completeness, quality, adherence
    5. Return highest-scoring candidate

    Falls back to first candidate if critique fails.
    """
    all_providers = _build_provider_list()

    # Step 1: Select diverse generators
    generators = _select_diverse_providers(all_providers, n=n_generators)
    if not generators:
        raise RuntimeError("Council: no providers available")

    gen_names = [f"{g['name']}:{g['model']}" for g in generators]
    logger.info(f"[council] Generators: {gen_names}")

    # Step 2: Generate candidates
    candidates = []
    for gen in generators:
        result = _call_provider(gen, prompt, system, temperature, max_tokens=4096)
        if result and result.get("content"):
            candidates.append(result)

    if not candidates:
        raise RuntimeError("Council: all generators failed")
    if len(candidates) == 1:
        logger.info("[council] Only 1 candidate — returning without critique")
        return candidates[0]

    # Step 3: Select critic from a different model family
    gen_families = {_model_family(g["model"]) for g in generators}
    critics = _select_diverse_providers(all_providers, n=1, exclude_families=gen_families)
    if not critics:
        critics = _select_diverse_providers(all_providers, n=1)

    critic_provider = critics[0]
    logger.info(f"[council] Critic: {critic_provider['name']}:{critic_provider['model']}")

    # Step 4: Build critique prompt with numeric scoring
    candidate_blocks = []
    for i, c in enumerate(candidates, 1):
        candidate_blocks.append(
            f"--- CANDIDATE {i} ({c['provider']}:{c['model']}) ---\n{c['content'][:3000]}"
        )

    critique_prompt = (
        f"You are evaluating {len(candidates)} candidate outputs for this task:\n"
        f"{task_description}\n\n"
        "Rate each candidate 0-100 on:\n"
        "1. ACCURACY: Does it follow ALL instructions? No banned phrases, no fabrication?\n"
        "2. COMPLETENESS: Are all required sections/structure present?\n"
        "3. QUALITY: Active voice, specific metrics, no filler, proper formatting (\\textbf preserved)?\n"
        "4. ADHERENCE: Does it match the specific job description, not generic?\n\n"
        "Average the four dimensions into a single score per candidate.\n\n"
        + "\n\n".join(candidate_blocks)
        + "\n\nReturn ONLY a JSON array of integer scores in candidate order, e.g. [85, 72]. No other text."
    )

    try:
        critique = _call_provider(
            critic_provider, critique_prompt,
            system="You are an impartial AI output evaluator. Return only valid JSON.",
            temperature=0, max_tokens=100,
        )
        if not critique:
            logger.warning("[council] Critic call failed, returning first candidate")
            return candidates[0]

        scores = _parse_critic_scores(critique["content"], len(candidates))
        if scores:
            best_idx = max(range(len(scores)), key=lambda i: scores[i])
            winner = candidates[best_idx]
            logger.info(
                f"[council] Scores: {scores}, Winner: candidate {best_idx + 1} "
                f"({winner['provider']}:{winner['model']}) score={scores[best_idx]}"
            )
            return winner
        else:
            logger.warning(f"[council] Could not parse critic scores: {critique['content'][:200]}")
            return candidates[0]
    except Exception as e:
        logger.warning(f"[council] Critique failed ({e}), returning first candidate")
        return candidates[0]


# ---------------------------------------------------------------------------
# ai_complete_cached — with Supabase cache
# ---------------------------------------------------------------------------

def ai_complete_cached(prompt: str, system: str = "", cache_hours: int = 72, temperature: float = 0.3) -> dict:
    """AI complete with Supabase cache. Returns dict with content, provider, model."""
    cache_key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    db = get_supabase()

    cached = db.table("ai_cache").select("response, provider, model") \
        .eq("cache_key", cache_key) \
        .gte("expires_at", datetime.utcnow().isoformat()).execute()
    if cached.data:
        return {
            "content": cached.data[0]["response"],
            "provider": cached.data[0].get("provider", "cache"),
            "model": cached.data[0].get("model", "cache"),
        }

    result = ai_complete(prompt, system, temperature=temperature)

    db.table("ai_cache").upsert({
        "cache_key": cache_key,
        "response": result["content"],
        "provider": result["provider"],
        "model": result["model"],
        "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
    }, on_conflict="cache_key").execute()

    return result
