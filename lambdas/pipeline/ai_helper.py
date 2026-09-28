"""AI helper for Lambda functions — real council with diverse generators and numeric critic scoring."""
import hashlib
import json
import logging
import os
import random
import time
import re
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()

# Lazy SSM client — created on first call, reused thereafter.
# Module-level boto3.client("ssm") forces AWS_DEFAULT_REGION on every importer
# (incl. test harnesses + the Deploy Readiness CI smoke step) even when SSM
# isn't actually used. Lazy init defers credential/region resolution until
# we need it.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def get_param(name):
    """Resolve a `/naukribaba/...` config value.

    Precedence: environment variable first, SSM Parameter Store second. The
    env var name is derived deterministically from `name`'s last path
    segment — e.g. `/naukribaba/GROQ_API_KEY` -> `GROQ_API_KEY` — so callers
    never need a separate mapping table.

    Why: this is the single choke point every provider-key/Supabase-cred
    lookup goes through (council providers, get_supabase()). CI's AI Eval
    Gate (.github/workflows/ci.yml `ai-eval` job) runs under an IAM user
    with no `ssm:GetParameter` grant — deliberately; it should need no AWS
    access at all — so it supplies these as plain `env:` secrets instead.
    Local runs benefit the same way (no SSM round-trip needed).

    This does NOT change behavior for any deployed Lambda: every pipeline
    Lambda's IAM role already grants ssm:GetParameter (see template.yaml)
    and none of them sets these param basenames as plain environment
    variables, so the env lookup below misses and SSM stays the sole
    source of truth there — exactly as before this change. (JobHuntApi is
    the one Lambda whose `Environment.Variables` already mirrors this same
    name mapping — see template.yaml — so for it this makes an
    already-intentional env var actually take effect instead of being
    silently ignored in favor of an extra SSM round-trip.)
    """
    env_name = name.rsplit("/", 1)[-1]
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


# ---------------------------------------------------------------------------
# Provider pool
# ---------------------------------------------------------------------------

# Token budget for the council's critic call. Reasoning models (gpt-oss et al)
# emit their chain-of-thought into a separate `reasoning` field and only then
# write `content`. At the old budget of 100 the whole allowance was consumed by
# reasoning, content came back empty, _call_provider treated that as a failure,
# and the council silently degraded to "return the first candidate". The critic
# only needs to emit a short JSON array, so the headroom is almost entirely for
# reasoning tokens.
CRITIC_MAX_TOKENS = 1024

# System prompt for the critic call. Shared by the sequential council below
# (council_complete) and the LangGraph port (agents.nodes.critique_node) so
# the two engines can't drift while they run side by side during migration.
CRITIQUE_SYSTEM = "You are an impartial AI output evaluator. Return only valid JSON."


def _build_provider_list() -> list[dict]:
    """Build the full provider config list with all available models."""
    openrouter_headers = {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}
    openrouter_key = "/naukribaba/OPENROUTER_API_KEY"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
    # OpenRouter free tier, re-verified 2026-08-31. NOTE: the free pool shares a
    # per-account daily quota — without credits on the account these return
    # "429 free-models-per-day" regardless of which model is requested. They are
    # kept as council *depth*, not as the primary path; Groq carries the load.
    # Re-probed 2026-09-28 by scripts/probe_models.py at ~2,900 chars (a
    # realistic scoring prompt), requiring parseable JSON back -- not HTTP 200.
    #
    # REMOVED, all three confirmed dead or unusable in production that day:
    #   minimax/minimax-m3   404 -- model id no longer exists
    #   z-ai/glm-5.2         404 -- model id no longer exists
    #   google/gemma-4-31b-it 429 -- shared free-pool daily quota, exhausted
    #
    # Those three were the ONLY non-Groq families in the pool, so every
    # council run logged "[council] Critic call failed -- returning first
    # candidate": generators took Groq, the cross-family critic had nowhere
    # to go, and a 3-call council silently degraded to one unreviewed
    # candidate. The replacements below each add a distinct working family,
    # which is what the critic actually needs.
    # Chosen for CAPABILITY first, family diversity second. The first cut of
    # this list optimised only for distinct families and the AI Eval Gate
    # caught it: tier_accuracy fell 63.2% -> 25.0%. Two of the four additions
    # were structurally unsuited to scoring a job description —
    # cohere/north-mini-code is a code-completion model, and liquid/lfm-2.5-2.6b
    # is a 2.6B model that took 63s to answer the probe. A council whose
    # generators and critic cannot reason about a JD produces confident
    # nonsense, which is worse than the dead-critic failure it replaced.
    #
    # Probe scores on one fixed JD+resume pair, 2026-09-28 (not ground truth —
    # the models genuinely disagree — but a 2.6B model scoring 25 where a 550B
    # scores 50 is signal about capability, not about the job):
    #   nemotron-3.5-lightning     65
    #   nemotron-3-ultra-550b      50
    #   nemotron-3-super-120b      48
    #   ling-3.0-flash-fin         45
    #   ling-3.0-flash-sante       40
    #   north-mini-code            30   <- dropped, code model
    #   lfm-2.5-2.6b               25   <- dropped, 2.6B and 63s
    openrouter_models = [
        "nvidia/nemotron-3.5-lightning:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "inclusionai/ling-3.0-flash-fin:free",
        "inclusionai/ling-3.0-flash-sante:free",
    ]

    # Groq is the primary provider — its own free tier is not shared with the
    # OpenRouter pool and every model below was probed successfully on
    # 2026-08-31 (latency in comments, measured on a scoring-shaped prompt).
    #
    # NVIDIA NIM was removed: every candidate model returned 404 "Function not
    # found" or timed out against this account's key. Re-add only after a live
    # probe passes — see tests/unit/test_ai_council_models.py.
    groq_url = "https://api.groq.com/openai/v1/chat/completions"
    providers = [
        {"name": "groq/gpt-oss-120b", "url": groq_url,
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "openai/gpt-oss-120b",
         "timeout": 90},   # ~800ms, strongest available
        {"name": "groq/qwen3.8-27b", "url": groq_url,
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "qwen/qwen3.8-27b",
         "timeout": 60},   # ~335ms, fastest
        {"name": "groq/gpt-oss-20b", "url": groq_url,
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "openai/gpt-oss-20b",
         "timeout": 60},   # ~800ms, same family as 120b (dedup handles it)
        # groq/compound REMOVED 2026-09-01. It answers a toy prompt but returns
        # 413 "Payload Too Large" on every real scoring call — it is an agentic
        # model whose internal tool calls carry a much lower payload allowance
        # than the plain chat models. In the 09-01 verification run it was the
        # ONLY remaining source of 413s while gpt-oss-120b and qwen3.8-27b
        # succeeded on the same prompts, so it was a guaranteed-failing hop on
        # every request. Re-add only if a probe passes at a realistic prompt
        # size (~3,800 tokens), not a one-word smoke test.
    ]
    for m in openrouter_models:
        providers.append({
            "name": f"openrouter/{m.split('/')[-1].split(':')[0]}",
            "url": openrouter_url, "key_param": openrouter_key, "model": m,
            "timeout": 90, "extra_headers": openrouter_headers,
        })
    # Paid Qwen-plus via Alibaba dashscope. Opt-in only — costs ~$0.005/call
    # at ~3-4k tokens. Audit on 2026-05-06 showed ~15-20 calls/day across the
    # pipeline (mostly via fallback path) → ~$3/month. Default disabled
    # because the 7 free providers above are sufficient. Set
    # ENABLE_PAID_QWEN=true on the Lambda to re-enable.
    if os.environ.get("ENABLE_PAID_QWEN", "false").lower() == "true":
        providers.append(
            {"name": "qwen", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
             "key_param": "/naukribaba/QWEN_API_KEY", "model": "qwen-plus",
             "timeout": 90},
        )
    providers.extend(_registry_providers({p["model"] for p in providers}))
    return providers


def _registry_providers(already: set[str]) -> list[dict]:
    """Extra models from agents/model_registry.json, for rotation breadth.

    The point of a large pool is not more opinions per decision — the council
    still makes 3 calls. It is that 3 can be drawn from ~26 verified models
    across 8 families, so no single provider's rate limit or daily quota can
    take the council down. On 2026-09-28 the hand-maintained list held 8
    entries and 3 of them were dead at once, which was enough to collapse it.

    Every entry was proved by a live call at realistic prompt size (see
    scripts/probe_models.py); the registry records the date. Entries whose
    credentials are absent simply fail once and get cooled, which is why this
    does not try to pre-validate them.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "agents", "model_registry.json")
    try:
        with open(path) as fh:
            entries = json.load(fh).get("models", [])
    except Exception as e:  # a missing registry must not break the pipeline
        logger.warning(f"[ai] model registry unavailable, using core pool only: {e}")
        return []

    # DashScope is metered, unlike the free tiers above, so it stays behind the
    # same opt-in flag the single qwen-plus entry already uses.
    paid_qwen = os.environ.get("ENABLE_PAID_QWEN", "false").lower() == "true"

    extra = []
    for e in entries:
        if e["model"] in already:
            continue
        if e["provider"] == "qwen" and not paid_qwen:
            continue
        already.add(e["model"])
        entry = {
            "name": e["name"], "url": e["url"], "key_param": e["key_param"],
            "model": e["model"], "timeout": e.get("timeout", 90),
        }
        if e["provider"] == "openrouter":
            entry["extra_headers"] = {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}
        extra.append(entry)
    return extra


def _model_family(model: str) -> str:
    """Collapse model names to canonical family for dedup.

    Ensures council never picks two instances of the same underlying model
    (e.g. llama-3.3-70b from both Groq and NVIDIA).
    """
    m = model.lower().split("/")[-1]
    m = m.replace(":free", "")
    for prefix in ("deepseek", "llama-3.3", "llama-3.1", "llama-4", "qwen3",
                    "qwen-plus", "qwen-turbo", "qwen-max", "gpt-oss",
                    "mistral-small", "nemotron", "hermes", "gemma", "glm",
                    "minimax", "step"):
        if m.startswith(prefix):
            return prefix
    return m


# ---------------------------------------------------------------------------
# Provider rotation — the point of a large verified pool
# ---------------------------------------------------------------------------
# The pool is wide so the council can ROTATE off a provider that has hit a
# limit, rather than retrying into the same wall. Without this, 2026-09-28 saw
# every council call log "Critic call failed": the three non-Groq entries were
# 404, 404 and 429 simultaneously, and selection kept offering them.
#
# Cooldown scope is not the same for every failure, and getting it wrong is
# what makes rotation useless:
#
#   429  -> cool the ACCOUNT, not the model. OpenRouter's free pool shares one
#           daily quota across every model on it, so a 429 on gemma means glm
#           is equally unavailable. Groq's is a per-minute token budget, so it
#           recovers in a minute; OpenRouter's is daily, so it does not.
#   404  -> cool the MODEL, for a long time. Free model ids are withdrawn
#           without notice (minimax-m3, glm-5.2); the account is fine.
#   5xx / timeout -> cool the MODEL briefly; probably transient.
#
# State is module-level, so it survives across invocations while a Lambda
# container stays warm — which is exactly the window a per-minute budget
# cares about. A cold start simply starts over, which is correct: the limit
# it was avoiding has almost certainly reset by then.
_COOLDOWNS: dict[str, float] = {}

_RATE_LIMIT_COOLDOWN_S = {
    "groq": 90,          # 8k tokens/min — recovers within the minute
    "openrouter": 1800,  # shared free-pool DAILY quota; long, but not all day
    "qwen": 120,
    "nvidia": 300,
    "deepseek": 300,
}
_MODEL_GONE_COOLDOWN_S = 6 * 3600   # a withdrawn model id is not coming back today
_TRANSIENT_COOLDOWN_S = 120


def _account_of(provider: dict) -> str:
    """The billing/quota bucket a provider draws from."""
    return provider["name"].split("/", 1)[0]


def _cool_down(key: str, seconds: int) -> None:
    until = time.time() + seconds
    if _COOLDOWNS.get(key, 0) < until:
        _COOLDOWNS[key] = until


def note_provider_failure(provider: dict, status: int | None) -> None:
    """Record a failure so selection can route around it."""
    account = _account_of(provider)
    if status == 429:
        secs = _RATE_LIMIT_COOLDOWN_S.get(account, 300)
        _cool_down(f"account:{account}", secs)
        logger.info(f"[ai] cooling account '{account}' for {secs}s after 429")
    elif status == 404:
        _cool_down(f"model:{provider['name']}", _MODEL_GONE_COOLDOWN_S)
        logger.info(f"[ai] cooling model '{provider['name']}' — 404, id likely withdrawn")
    else:
        _cool_down(f"model:{provider['name']}", _TRANSIENT_COOLDOWN_S)


def note_provider_success(provider: dict) -> None:
    """A success proves both the account and the model are usable again."""
    _COOLDOWNS.pop(f"account:{_account_of(provider)}", None)
    _COOLDOWNS.pop(f"model:{provider['name']}", None)


def _is_available(provider: dict, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    return (_COOLDOWNS.get(f"account:{_account_of(provider)}", 0) <= now
            and _COOLDOWNS.get(f"model:{provider['name']}", 0) <= now)


def _reset_cooldowns() -> None:
    """Test hook — module state would otherwise leak between tests."""
    _COOLDOWNS.clear()


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

    # Prefer providers that are not cooling. Fail OPEN when every candidate is
    # cooling: a stale cooldown estimate must never leave the council with
    # nothing to call, and the worst case is one wasted request that re-cools
    # the provider anyway.
    usable = [p for p in providers if _is_available(p)]
    if not usable:
        logger.warning("[ai] every provider is cooling — ignoring cooldowns for this pick")
        usable = list(providers)

    shuffled = list(usable)
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
            choice = resp.json()["choices"][0]
            message = choice.get("message", {})
            content = message.get("content")
            if not content:
                # A reasoning model that ran out of budget looks identical to a
                # broken provider unless we say so explicitly. finish_reason
                # 'length' plus a populated `reasoning` field means the model
                # worked fine and max_tokens was simply too low.
                if choice.get("finish_reason") == "length" and message.get("reasoning"):
                    logger.warning(
                        "[ai] %s returned only reasoning tokens — max_tokens=%s too low "
                        "for a reasoning model, raise the budget",
                        provider["name"], max_tokens,
                    )
                else:
                    logger.warning(f"[ai] {provider['name']} returned empty content")
                return None
            note_provider_success(provider)
            return {"content": content, "provider": provider["name"], "model": provider["model"]}
        elif resp.status_code == 429:
            logger.warning(f"[ai] {provider['name']} rate limited")
            note_provider_failure(provider, 429)
        else:
            logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
            note_provider_failure(provider, resp.status_code)
        return None
    except Exception as e:
        logger.warning(f"[ai] {provider['name']} failed: {e}")
        note_provider_failure(provider, None)
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

def build_critique_prompt(candidates: list[dict], task_description: str) -> str:
    """Build the critic's numeric-scoring prompt.

    Single source for this rubric — shared by council_complete below and by
    the LangGraph port's critique_node (agents/nodes.py imports this
    function rather than keeping its own copy) so the two engines can't
    silently drift while they run side by side during migration.
    """
    candidate_blocks = []
    for i, c in enumerate(candidates, 1):
        candidate_blocks.append(
            f"--- CANDIDATE {i} ({c['provider']}:{c['model']}) ---\n{c['content'][:3000]}"
        )

    return (
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


def _council_engine() -> str:
    """Which council implementation to use: 'legacy' or 'langgraph'.

    Defaults to legacy so a deploy never silently changes behaviour; the flag
    is flipped only after the parity test and a live smoke run pass.
    """
    return os.environ.get("COUNCIL_ENGINE", "legacy").strip().lower()


def council_complete(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
    task: str = "default",
    base_skills: str = "",
    base_body: str = "",
    header_markers: list[str] | None = None,
) -> dict:
    """Generate candidates from diverse models, pick the best by critic score.

    `task` selects the guardrail policy (guardrails/policy.py) that the
    LangGraph engine's guard_input_node/guard_output_node apply -- "tailor",
    "cover_letter", "score", or the "default" fallback used when a caller
    passes nothing (so existing callers keep their pre-guardrail behaviour
    unless they opt in). `base_skills`/`base_body`/`header_markers` are
    extra context guard_output_node needs to evaluate fabrication and
    formatting-preservation checks against the CALLER's own base resume
    rather than an empty baseline; pass them whenever the caller actually
    has them and the check is meaningful against what the graph evaluates
    (see tailor_resume.py's council_complete call for a case where one of
    these three is deliberately withheld, and why).
    """
    if _council_engine() == "langgraph":
        try:
            from agents.graph import council_complete_langgraph  # flat — pytest / zip Lambda
        except ImportError:
            # Container image (Dockerfile.lambda ships `lambdas/` as a real
            # package): this module is imported as `lambdas.pipeline.ai_helper`,
            # and there is no flat `agents` on the path there — only
            # `lambdas.pipeline.agents` resolves.
            from lambdas.pipeline.agents.graph import council_complete_langgraph
        return council_complete_langgraph(
            prompt, system, task_description, n_generators, temperature,
            task=task, base_skills=base_skills, base_body=base_body,
            header_markers=header_markers,
        )
    # Legacy has no guard nodes at all -- _council_complete_legacy below never
    # imports guardrails, so `task`/`base_skills`/`base_body`/`header_markers`
    # have nothing to plug into on this path. Dropped here deliberately, not
    # forgotten: legacy is frozen pre-guardrail behaviour, kept only as the
    # COUNCIL_ENGINE=legacy escape hatch and the parity test's baseline, and
    # is not a candidate for picking up guard-policy awareness of its own.
    return _council_complete_legacy(
        prompt, system, task_description, n_generators, temperature
    )


def _council_complete_legacy(
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

    # Step 2: Generate candidates — each generator tries its assigned provider first,
    # then falls back through remaining providers to ensure we actually get output.
    candidates = []
    used_families = set()
    for gen in generators:
        result = _call_provider(gen, prompt, system, temperature, max_tokens=4096)
        if result and result.get("content"):
            candidates.append(result)
            used_families.add(_model_family(gen["model"]))
        else:
            # Primary provider failed — try others from different families
            for fallback in all_providers:
                fb_fam = _model_family(fallback["model"])
                if fb_fam in used_families or fb_fam == _model_family(gen["model"]):
                    continue
                result = _call_provider(fallback, prompt, system, temperature, max_tokens=4096)
                if result and result.get("content"):
                    candidates.append(result)
                    used_families.add(fb_fam)
                    logger.info(f"[council] Generator fallback: {fallback['name']} succeeded")
                    break

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
    critique_prompt = build_critique_prompt(candidates, task_description)

    try:
        critique = _call_provider(
            critic_provider, critique_prompt,
            system=CRITIQUE_SYSTEM,
            temperature=0, max_tokens=CRITIC_MAX_TOKENS,
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

def ai_complete_cached(
    prompt: str,
    system: str = "",
    cache_hours: int = 72,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict:
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

    result = ai_complete(prompt, system, temperature=temperature, max_tokens=max_tokens)

    db.table("ai_cache").upsert({
        "cache_key": cache_key,
        "response": result["content"],
        "provider": result["provider"],
        "model": result["model"],
        "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
    }, on_conflict="cache_key").execute()

    return result
