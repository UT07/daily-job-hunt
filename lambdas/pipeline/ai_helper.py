"""Lightweight AI helper for Lambda functions. Calls AI providers via httpx."""
import hashlib
import logging
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


def ai_complete(prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.3) -> dict:
    """Call AI provider with failover chain across multiple providers and models.

    Order: Groq → NVIDIA NIM → OpenRouter (rotates through 5 verified free models)
    → Qwen. Each provider/model combo is tried once; on failure
    (rate limit, timeout, 404, empty content), the next is attempted.

    DeepSeek direct API removed — credits exhausted (402). DeepSeek models
    are still accessible via NVIDIA NIM.

    OpenRouter models verified working 2026-04-05. The dead
    `google/gemini-2.0-flash-exp:free` was removed from OpenRouter (404 "No
    endpoints found"). Reasoning-heavy models like `openai/gpt-oss-*:free`
    excluded because they burn max_tokens in `reasoning` with null content.
    """
    openrouter_headers = {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}
    openrouter_key = "/naukribaba/OPENROUTER_API_KEY"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
    # Rotation order: pick verified-responsive first, then next-best by rate-limit risk
    openrouter_models = [
        "qwen/qwen3.6-plus:free",                      # 1M ctx, clean instruction-following
        "nvidia/nemotron-3-super-120b-a12b:free",      # 262K ctx, 120b params
        "meta-llama/llama-3.3-70b-instruct:free",      # reliable when not rate-limited
        "z-ai/glm-4.5-air:free",                       # 131K ctx
        "google/gemma-3-27b-it:free",                  # 131K ctx fallback
    ]

    providers = [
        {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "llama-3.3-70b-versatile",
         "timeout": 60},
        {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
         "key_param": "/naukribaba/NVIDIA_API_KEY", "model": "meta/llama-3.3-70b-instruct",
         "timeout": 120},
    ]
    # Expand OpenRouter into one provider entry per model so the failover chain
    # retries each model independently when any single one is rate-limited.
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

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    for provider in providers:
        try:
            api_key = get_param(provider["key_param"])
            if not api_key or api_key == "mock-value":
                continue  # Skip providers without keys configured

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if "extra_headers" in provider:
                headers.update(provider["extra_headers"])

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
                    # Some reasoning-heavy models return content=null when all
                    # max_tokens are spent in the `reasoning` field. Skip them.
                    logger.warning(f"[ai] {provider['name']} returned empty content, trying next")
                    continue
                logger.info(f"[ai] {provider['name']}/{provider['model']} succeeded")
                return {"content": content, "provider": provider["name"], "model": provider["model"]}
            elif resp.status_code == 429:
                logger.warning(f"[ai] {provider['name']} rate limited, trying next")
            else:
                logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
        except Exception as e:
            last_error = e
            logger.warning(f"[ai] {provider['name']} failed: {e}")

    raise RuntimeError(f"All {len(providers)} AI providers failed. Last error: {last_error}")


def council_complete(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
) -> dict:
    """Generate multiple AI responses and pick the best via critique.

    Calls n_generators providers independently, then uses a critic to evaluate
    and select the highest-quality output. Falls back to the first successful
    response if the critique step fails.
    """
    import json

    candidates = []

    def _generate(attempt_idx):
        """Generate one candidate, skipping the first `attempt_idx` providers."""
        try:
            return ai_complete(prompt, system, temperature=temperature)
        except RuntimeError:
            return None

    # Generate candidates (sequential to avoid provider key caching issues)
    for i in range(n_generators):
        result = _generate(i)
        if result and result.get("content"):
            candidates.append(result)

    if not candidates:
        raise RuntimeError("Council: all generators failed")
    if len(candidates) == 1:
        return candidates[0]

    # Critique: ask AI to pick the better candidate
    critique_prompt = f"""You are evaluating {len(candidates)} resume tailoring attempts. Pick the BETTER one.

EVALUATION CRITERIA (score each 1-10):
1. KEYWORD COVERAGE: Does the resume address the top JD keywords?
2. SECTION COMPLETENESS: Are all 6 sections present (Summary, Skills, Experience, Projects, Education, Certifications)?
3. WRITING QUALITY: Active voice, specific metrics, no filler phrases?
4. PAGE LENGTH: Is it approximately 850-1000 words (2 pages)?
5. TRUTHFULNESS: Does it avoid fabricating experience?

{task_description}

"""
    for i, c in enumerate(candidates):
        critique_prompt += f"\n--- CANDIDATE {i+1} (by {c['provider']}:{c['model']}) ---\n{c['content'][:3000]}\n"

    critique_prompt += "\nReturn ONLY a JSON object: {\"winner\": 1 or 2, \"reason\": \"brief explanation\"}"

    try:
        critique = ai_complete(critique_prompt, system="You are a resume quality evaluator. Return only valid JSON.", temperature=0)
        text = critique["content"].strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        winner_idx = int(result.get("winner", 1)) - 1
        winner_idx = max(0, min(winner_idx, len(candidates) - 1))
        winner = candidates[winner_idx]
        logger.info(f"[council] Winner: candidate {winner_idx+1} ({winner['provider']}:{winner['model']}). Reason: {result.get('reason','')[:100]}")
        return winner
    except Exception as e:
        logger.warning(f"[council] Critique failed ({e}), returning first candidate")
        return candidates[0]


def ai_complete_cached(prompt: str, system: str = "", cache_hours: int = 72, temperature: float = 0.3) -> dict:
    """AI complete with Supabase cache. Returns dict with content, provider, model."""
    cache_key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    db = get_supabase()

    # Check cache
    cached = db.table("ai_cache").select("response, provider, model") \
        .eq("cache_key", cache_key) \
        .gte("expires_at", datetime.utcnow().isoformat()).execute()
    if cached.data:
        return {
            "content": cached.data[0]["response"],
            "provider": cached.data[0].get("provider", "cache"),
            "model": cached.data[0].get("model", "cache")
        }

    # Call AI
    result = ai_complete(prompt, system, temperature=temperature)

    # Cache result
    db.table("ai_cache").upsert({
        "cache_key": cache_key,
        "response": result["content"],
        "provider": result["provider"],
        "model": result["model"],
        "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
    }, on_conflict="cache_key").execute()

    return result
