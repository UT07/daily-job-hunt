"""AI helper for Lambda functions — real council with diverse generators and numeric critic scoring."""
import hashlib
import json
import logging
import os
import random
import time
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

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
# The critic's ANSWER is tiny — a numeric verdict per candidate — but a
# reasoning model spends its budget thinking before it emits anything, and
# _call_provider treats empty content as a failure. So an under-budgeted critic
# does not return a worse verdict; it returns NO verdict, and the council
# silently falls back to candidate 1.
#
# That is what has been happening. Measured over 3 days of production logs
# (2026-09-27..30), across naukribaba-tailor-resume and
# naukribaba-generate-cover-letter:
#
#     adjudicated            24 / 107   22%
#     critic_call_failed     61 / 107   57%   <-- this
#     critic_unparseable     12 / 107   11%
#     single_candidate       10 / 107    9%
#     no_critic_family        0 / 107    0%
#
# agents/model_registry.json records min_output_tokens: 3000 for
# openai/gpt-oss-120b and openai/gpt-oss-20b — Groq's primary entries, so a
# frequent critic pick — with the note "Reasoning models consume the budget
# internally before emitting". At 1024 they never reach the answer.
#
# The same fix was applied to the GENERATOR budget (_REASONING_HEADROOM_TOKENS,
# rewrite_budget) and the critic was left flat. Two of everything.
#
# Sized per model rather than raised globally, because Groq bills
# prompt_tokens + max_tokens against 8k/minute. The critique prompt truncates
# each candidate to 3000 chars (build_critique_prompt), so two candidates plus
# the rubric is roughly 2k tokens; 2k + 3256 stays inside 8k, while a flat
# raise for every provider would not have been checkable.
CRITIC_MAX_TOKENS = 1024

# Room for the verdict itself once a reasoning model has finished thinking.
# The answer is a few numbers and a sentence.
CRITIC_ANSWER_TOKENS = 256


def critic_budget(provider: dict | None) -> int:
    """Output budget for THIS critic, respecting its reasoning floor.

    Falls back to CRITIC_MAX_TOKENS for any model the registry does not know,
    so an unrecognised provider behaves exactly as before.
    """
    model = (provider or {}).get("model") or ""
    floor = 0
    try:
        from agents.registry import all_models

        for entry in all_models():
            if entry.get("model") == model:
                floor = int(entry.get("min_output_tokens") or 0)
                break
    except Exception:  # registry unreadable in some deploy shapes
        floor = 0
    return min(max(CRITIC_MAX_TOKENS, floor + CRITIC_ANSWER_TOKENS),
               MAX_OUTPUT_TOKENS_CAP)

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
    #   ling-3.0-flash-fin         45   <- REMOVED 2026-09-28, withdrawn from
    #                                      OpenRouter; absent from the live
    #                                      /models listing, 404 on every call
    #   ling-3.0-flash-sante       40   <- REMOVED 2026-10-09, 404 "id withdrawn"
    #                                      on every call from the production API
    #   north-mini-code            30   <- dropped, code model
    #   lfm-2.5-2.6b               25   <- dropped, 2.6B and 63s
    openrouter_models = [
        "nvidia/nemotron-3.5-lightning:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
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
    # Google AI Studio — the council's only quota independent of Groq and
    # OpenRouter, and free. Sustained-load verified 24/24 before being trusted;
    # see the registry's _GEMINI note for why that mattered.
    #
    # ORDER MATTERS, and this sits AHEAD of the OpenRouter block deliberately.
    # OpenRouter's free pool is capped per ACCOUNT per day — 50 requests
    # without credits on the account — so all of its entries go 429 together
    # and stay that way for the rest of the day. Measured 2026-09-28: the cap
    # was exhausted, every OpenRouter model returned
    # "429 free-models-per-day", and because Gemini was appended after them it
    # sat at index 8. Each call spent five doomed hops (up to 90s of timeout
    # budget apiece) before reaching the provider that answers in ~0.9s, and
    # the CI eval gate reported families_served: ["groq"] on a pool that had a
    # working second family the entire time.
    #
    # Groq stays first: ~300-800ms, its own quota, and the strongest models.
    # Gemini is the first FAILOVER. OpenRouter is depth behind both.
    providers.append({
        "name": "gemini/gemini-3.5-flash-lite",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_param": "/naukribaba/GEMINI_API_KEY",
        "model": "gemini-3.5-flash-lite",
        "timeout": 60,
    })

    # Cerebras — a fifth independent quota, and the largest per-minute token
    # budget anywhere in this pool.
    #
    # The pool's weakness has never been entry count; it is that the entries
    # sit on a handful of ACCOUNTS and two of them carry the load. Groq's free
    # tier is 8,000 tokens per MINUTE, which a tailoring call (whole base
    # resume in, whole body out) can spend by itself — see rewrite_budget's
    # note. OpenRouter's free pool shares ONE daily allowance across every
    # model on it, so its entries die as a unit; measured 2026-09-29 against
    # the live keys, all four failed in the same sweep (two 404, two 429).
    #
    # Cerebras' published free-tier limits, read 2026-09-30 from
    # inference-docs.cerebras.ai/support/rate-limits:
    #
    #   gpt-oss-120b   5 RPM   30k TPM   1M TPH   1M TPD   ~3,000 tok/s
    #   qwen-3.8-27b   5 RPM   30k TPM   1M TPH   1M TPD   ~1,850 tok/s
    #
    # 30k tokens/minute is ~4x Groq's, on a quota shared with nothing else in
    # the chain. Both models are ones this repo has already probed successfully
    # on another host — gpt-oss-120b is the council's primary Groq entry
    # ("strongest available, ~800ms") and qwen3.8-27b its fastest ("~335ms,
    # clean JSON") — so the new quota arrives without new capability risk. That
    # also means Cerebras adds no new FAMILY: it is depth, not diversity, and
    # _model_family deliberately collapses each pair (see the qwen- hyphen note
    # in that function).
    #
    # NOT probed against a live key: this repo holds no Cerebras credential, so
    # these two ids come from the vendor's own docs rather than from
    # scripts/probe_models.py. They are therefore absent from
    # agents/model_registry.json, whose contract is "proved by a live call" —
    # add them there after a probe, not before. Until the key exists every call
    # here is a get_param miss, which _call_provider already treats as a skip.
    #
    # Position: after Gemini, ahead of NVIDIA and OpenRouter. Gemini is the one
    # failover measured under sustained load (24/24 at ~0.9s) and keeps its
    # slot; an unprobed provider should not displace it. Ahead of NVIDIA (4/6
    # under concurrency) and OpenRouter (50 requests/day without credits) on
    # the published limits above.
    # RETIRED 2026-10-08, on the measurement the note below asked for.
    # `qwen-3.8-27b` is gone from this list; the comment above describing both
    # ids is kept because it explains how they got here.
    #
    # Cerebras is now probed -- by production, over the 212-résumé batch of
    # 2026-10-07, which is the realistic size the model-rot lesson calls for:
    #
    #   cerebras/qwen-3.8-27b   385 appearances   109 "returned only reasoning
    #                                             tokens" (68 at max_tokens=8192,
    #                                             41 at 1024)
    #   groq/qwen3.8-27b        365 appearances     0
    #
    # Same weights, same family, comparable exposure, 109 failures against 0.
    # So the defect is Cerebras' SERVING of that model, not the model -- which
    # is why the fix is to drop this host's entry and not the family. Varying
    # the host while holding the model fixed is what made that readable at all
    # (CLAUDE.md #15); the raw 109 looks like a budget problem, and it is not:
    # it fails at 8192 as readily as at 1024, so a larger budget buys nothing.
    #
    # The selection counts matter as much as the failures: concluding Groq's
    # copy is healthy because its failures are absent would be wrong if it had
    # simply never been tried. 365 appearances says it was.
    #
    # `cerebras/gpt-oss-120b` STAYS: 326 appearances, 7 of the same failure
    # (2.1%), against groq/gpt-oss-120b's 384 and 0. Degraded, not broken, and
    # it is an independent quota.
    #
    # Family diversity is unaffected -- 5 families before and after, because
    # _model_family collapses the qwen pair and groq/qwen3.8-27b keeps that
    # family on the host that works.
    cerebras_url = "https://api.cerebras.ai/v1/chat/completions"
    for cerebras_model in ("gpt-oss-120b",):
        providers.append({
            "name": f"cerebras/{cerebras_model}",
            "url": cerebras_url,
            "key_param": "/naukribaba/CEREBRAS_API_KEY",
            "model": cerebras_model,
            # 60s: same budget as the Groq entries serving these same two
            # models. Cerebras advertises 1,850-3,000 tok/s, so a full resume
            # body should land well inside this; revise on measurement, not on
            # the advertised figure.
            "timeout": 60,
        })

    # NVIDIA NIM — the fourth independent quota, and the reason it is here
    # rather than disabled is a correction worth recording.
    #
    # It was disabled earlier on 2026-09-28 with the note "503s under sustained
    # load ... while costing 75s per attempt". Re-measured the same day after
    # the VPN that NVIDIA was throttling came off:
    #
    #   sequential, 4 calls x 3 models   12/12   3-5s / 5-11s / 21-26s
    #   concurrent, 6 at once             4/6    503s returned in ~0.5s
    #
    # The 503s are real; the cost estimate was not. A hop that answers two
    # thirds of the time and fails in half a second is nearly free, and this
    # is an account whose limits are shared with nothing else in the chain.
    # Placed after Gemini (0.9s, always answers) and ahead of OpenRouter
    # (50 requests/day without credits, routinely exhausted).
    #
    # timeout=45: successes land in 3-5s and failures in 0.5s, so the old 60-75s
    # budget only ever paid for a hang.
    providers.append({
        "name": "nvidia/nemotron-3-super-120b-a12b",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_param": "/naukribaba/NVIDIA_API_KEY",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "timeout": 45,
    })
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
        # Verified != suitable. Every entry was proved to RESPOND correctly at
        # realistic prompt size; that says nothing about whether it can reason
        # about a job description. Skipping this check is how a code-completion
        # model and a 2.6B model walked back into the council through the side
        # door after being removed from the list above, and the AI Eval Gate
        # measured tier_accuracy stuck at 25.0% against a 63.2% baseline.
        if not e.get("suitable_for_scoring", True):
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
    # One model, two host spellings. Cerebras serves Qwen 3.8 27B as
    # `qwen-3.8-27b`; Groq and DashScope serve the same weights as
    # `qwen3.8-27b`. The prefix table below matches `qwen3`, so the hyphenated
    # id fell through to `return m` and became its own family — which would let
    # _select_diverse_providers pick BOTH as "distinct" generators and have the
    # council review its own answer. Only a hyphen followed by a DIGIT is
    # collapsed, so DashScope's qwen-plus / qwen-turbo / qwen-max (real,
    # separate entries in the table) are untouched.
    m = re.sub(r"^qwen-(?=\d)", "qwen", m)
    for prefix in ("gemini", "deepseek", "llama-3.3", "llama-3.1", "llama-4", "qwen3",
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
#   429  -> scope depends on how the VENDOR meters, read from its docs
#           (2026-10-08) rather than assumed. OpenRouter's free pool shares
#           one daily quota across every model on the account, so a 429 on
#           gemma means glm is equally unavailable: ACCOUNT. Groq and Gemini
#           meter each model separately: MODEL. See
#           _PER_MODEL_RATE_LIMIT_ACCOUNTS for the quotes.
#   401
#   402
#   403  -> the KEY is missing, invalid or unfunded: cool the ACCOUNT for
#           hours and log at ERROR. Every model on it is equally dead and it
#           does not recover in two minutes. Except a moderation 403, which
#           is about one request.
#   404
#   410  -> cool the MODEL, for a long time. Free model ids are withdrawn
#           without notice (minimax-m3, glm-5.2) and hosted ones are retired on
#           a published date; the account is fine either way. 410 was missing
#           here until 2026-09-29, so an end-of-lifed model fell to the
#           transient branch and was retried every two minutes forever —
#           precisely what this cooldown table exists to prevent. Measured:
#           NVIDIA's meta/llama-3.3-70b-instruct returns
#           410 "has reached its end of life", and it had produced 125 of the
#           last 500 tailorings before it died.
#   5xx / timeout -> cool the MODEL briefly; probably transient.
#
# State is module-level, so it survives across invocations while a Lambda
# container stays warm — which is exactly the window a per-minute budget
# cares about. A cold start simply starts over, which is correct: the limit
# it was avoiding has almost certainly reset by then.
_COOLDOWNS: dict[str, float] = {}

_RATE_LIMIT_COOLDOWN_S = {
    "groq": 90,          # 8k tokens/min — recovers within the minute
    # Cerebras rate-limits with a token bucket that "replenishes continuously
    # rather than resetting at fixed intervals" (its own docs), at 5 RPM /
    # 30k TPM against a 1M-token daily cap. The limit a real run trips is
    # therefore the per-minute one, which clears in about a minute — the same
    # shape as Groq's, so the same window. Leaving it out would take the 300s
    # unknown-account default and bench a healthy provider five times longer
    # than needed. A Cerebras 429 that DOES name a per-day cap still gets the
    # until-midnight treatment: _rate_limit_cooldown_seconds decides from the
    # response body, not from the account.
    "cerebras": 90,
    # Fallback for a 429 whose body does NOT name a per-day quota — i.e. a
    # short throttle. The daily case is detected from the body and cooled until
    # UTC midnight instead; see _rate_limit_cooldown_seconds.
    "openrouter": 1800,
    "qwen": 120,
    "nvidia": 300,
    "deepseek": 300,
}
_MODEL_GONE_COOLDOWN_S = 6 * 3600   # a withdrawn model id is not coming back today
_TRANSIENT_COOLDOWN_S = 120
# A bad, unfunded or missing key. It used to take the 120s transient window,
# so a dead key was re-probed thirty times an hour per model, and logged at
# WARNING alongside every ordinary failover. A key does not fix itself; a
# human has to, so the cooldown spans most of a warm container's life.
_CREDENTIAL_COOLDOWN_S = 6 * 3600

# Accounts whose rate limits are metered PER MODEL, so a 429 cools only the
# model that returned it. Each entry is here on a vendor's own documentation,
# read 2026-10-08:
#
#   groq    console.groq.com/docs/rate-limits: "Rate limits apply at the
#           organization level, not individual users", with the limits table
#           keyed by MODEL ID (openai/gpt-oss-120b and qwen/qwen3.8-27b each
#           have their own 30 RPM / 1K RPD / 8K TPM / 200K TPD row). Its 429
#           body names the model: "Rate limit reached for model `...` in
#           organization `...`".
#   gemini  ai.google.dev/gemini-api/docs/rate-limits: "Rate limits are
#           applied per project, not per API key" and "Limits vary depending
#           on the specific model being used"; the quota ids say it outright,
#           e.g. GenerateRequestsPerDayPerProjectPerModel.
#
# NOT here, deliberately:
#   openrouter  docs/api-reference/limits: free-model limits are per ACCOUNT
#               (one `free_model_daily_requests` counter, 50/day under 10
#               credits). A 429 on one free model is a 429 on all of them.
#   cerebras    support/rate-limits says "Rate limits apply at the
#               organization level, not the user level, and vary based on the
#               model" — Groq's ambiguity without the per-model table or error
#               format that settles it. One Cerebras model is in the pool, so
#               the scope changes nothing today; revisit on measurement.
#
# This does not contradict the 2026-10-07 measurement (17b3b48: Groq took 41
# account-wide cooldowns and the live pool collapsed to Gemini). That
# measurement shows the ACCOUNT scope is what benched Groq's other models; it
# never showed that those models were themselves throttled. Groq's own docs
# say they are not. If they were, the cost is one fast 429 per sibling, which
# then cools that sibling too.
_PER_MODEL_RATE_LIMIT_ACCOUNTS = frozenset({"groq", "gemini"})

# Gemini documents that "Requests per day (RPD) quotas reset at midnight
# Pacific time", not UTC.
_PACIFIC_RESET_ACCOUNTS = frozenset({"gemini"})


def _account_of(provider: dict) -> str:
    """The billing/quota bucket a provider draws from."""
    return provider["name"].split("/", 1)[0]


def _cool_down(key: str, seconds: int) -> None:
    until = time.time() + seconds
    if _COOLDOWNS.get(key, 0) < until:
        _COOLDOWNS[key] = until


# Markers for a PER-DAY quota, matched on the kind of limit rather than on who
# sent it. The phrasing is OpenRouter's today; hard-coding the account would
# silently mistreat the next provider to adopt the same wording.
#
# Compared against the casefolded body. "perday" is Gemini's: its quota ids run
# words together (GenerateRequestsPerDayPerProjectPerModel), so an exhausted
# daily project quota matched none of the spaced or hyphenated forms and took
# the 300s default — re-probed ~250 times before the quota reset.
_DAILY_LIMIT_MARKERS = ("per-day", "per day", "perday", "daily limit", "requests/day")


def _seconds_to_utc_midnight(now: datetime | None = None) -> int:
    """Seconds until the next UTC midnight, when per-day quotas reset."""
    now = now or datetime.now(UTC)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Floor at a minute so a call landing a hair before midnight still backs off.
    return max(60, int((nxt - now).total_seconds()))


def _seconds_to_pacific_midnight(now: datetime | None = None) -> int:
    """Seconds until the next midnight in America/Los_Angeles (DST-aware)."""
    now = now or datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no tz database in this runtime: assume PST, which
        # resets an hour LATER than PDT, so the error is a longer wait, never
        # an early re-probe of a spent quota.
        local = now.astimezone(timezone(timedelta(hours=-8)))
    nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((nxt - local).total_seconds()))


def _is_daily_limit(detail: str) -> bool:
    folded = (detail or "").casefold()
    return any(m in folded for m in _DAILY_LIMIT_MARKERS)


def _rate_limit_cooldown_seconds(account: str, detail: str) -> int:
    """How long to route around `account` after it returned HTTP 429.

    `detail` is the response body, which is the only thing distinguishing the
    two very different limits a provider can mean by "429":

      * a PER-MINUTE throttle — clears in seconds, the provider is healthy, and
        a short cooldown is correct.
      * a PER-DAY quota — OpenRouter's free pool sends "Rate limit exceeded:
        free-models-per-day" and will not clear until UTC midnight. Measured
        2026-09-28: 50 requests/day without credits on the account, applied
        account-wide, so all eight OpenRouter models die together.

    Treating them alike was costing ~48 pointless retry sweeps a day. Treating
    EVERY 429 as the daily case would be the opposite error — a transient
    throttle would bench a healthy provider for up to 24 hours, and that may be
    the only live failover the council has. So the body decides, and an absent
    or unrecognised body takes the conservative short cooldown.
    """
    if _is_daily_limit(detail):
        if account in _PACIFIC_RESET_ACCOUNTS:
            return _seconds_to_pacific_midnight()
        return _seconds_to_utc_midnight()
    return _RATE_LIMIT_COOLDOWN_S.get(account, 300)


# OpenRouter documents 403 as "insufficient permissions, guardrail block, or
# moderation flag". The last two are about the REQUEST, so they must not bench
# the account the way a bad key does.
_REQUEST_SCOPED_403_MARKERS = ("moderation", "flagged", "guardrail")


def note_provider_failure(provider: dict, status: int | None, detail: str = "") -> None:
    """Record a failure so selection can route around it.

    `detail` carries the provider's response body when there is one. It is
    optional so existing call sites keep working unchanged.
    """
    account = _account_of(provider)
    if status == 429:
        secs = _rate_limit_cooldown_seconds(account, detail)
        if account in _PER_MODEL_RATE_LIMIT_ACCOUNTS:
            _cool_down(f"model:{provider['name']}", secs)
            logger.info(f"[ai] cooling model '{provider['name']}' for {secs}s after 429 "
                        f"({account} meters each model separately)")
        else:
            _cool_down(f"account:{account}", secs)
            logger.info(f"[ai] cooling account '{account}' for {secs}s after 429")
    elif status == 403 and any(m in (detail or "").casefold() for m in _REQUEST_SCOPED_403_MARKERS):
        _cool_down(f"model:{provider['name']}", _TRANSIENT_COOLDOWN_S)
        logger.warning(f"[ai] {provider['name']} refused this request (403, moderation/guardrail)")
    elif status in (401, 402, 403):
        _cool_down(f"account:{account}", _CREDENTIAL_COOLDOWN_S)
        logger.error(
            f"[ai] {account} rejected the API key ({status}) — missing, invalid or "
            f"unfunded. Every {account} model is benched for {_CREDENTIAL_COOLDOWN_S}s; "
            f"fix the key, this will not recover on its own."
        )
    elif status in (404, 410):
        _cool_down(f"model:{provider['name']}", _MODEL_GONE_COOLDOWN_S)
        logger.info(
            f"[ai] cooling model '{provider['name']}' — {status}, "
            f"id {'withdrawn' if status == 404 else 'retired (end of life)'}"
        )
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
    fill_same_family: bool = False,
) -> list[dict]:
    """Pick N providers from distinct model families.

    Shuffles before selection so different runs get different subsets.
    Excludes any families in exclude_families (used to pick critics that
    didn't generate).

    `fill_same_family` relaxes the one-per-family rule ONLY when distinct
    families cannot fill the request, and only for callers that ask. Measured
    over the 212-résumé batch of 2026-10-07:

        single_candidate   415
        adjudicated        129

    A 429 cools the whole ACCOUNT, so OpenRouter (daily quota) and Groq (41
    cooldowns x 90s) spend most of a fast batch benched, and the live pool
    collapses to Gemini alone. Gemini has FIVE models in the pool and
    `_model_family` folds them into one family, so a perfectly healthy Gemini
    contributed exactly ONE generator — and one candidate means nothing to
    adjudicate, no critic verdict, and a planning-laced body surviving to the
    hard gates.

    Two models from one family is weaker diversity than two families. It is
    much stronger than no comparison at all, which is what the strict rule
    delivers when the pool is degraded. The preference is unchanged: pass one
    still takes distinct families, so a healthy pool behaves exactly as before
    and this fills only what would otherwise be missing.

    Off by default, because the critic slot must NOT use it: a critic from the
    family that generated is not an independent reviewer. Neither critic
    picker (`agents/providers.select_critics`, `_council_complete_legacy`)
    relaxes; with no unused family they record `no_critic_family` instead.
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

    if fill_same_family and len(result) < n:
        # Same pool, same shuffle, same exclusions — only the one-per-family
        # rule is lifted. Identity, not equality: two pool entries can serve
        # the same model id on different hosts and both are usable.
        chosen = {id(p) for p in result}
        for p in shuffled:
            if id(p) in chosen or _model_family(p["model"]) in exclude_families:
                continue
            result.append(p)
            chosen.add(id(p))
            if len(result) >= n:
                break
        if len(result) > len(seen):
            logger.info(
                "[ai] only %d distinct famil%s available — filled %d generator "
                "slot(s) from the same family so there is something to adjudicate",
                len(seen), "y" if len(seen) == 1 else "ies", len(result) - len(seen),
            )
    return result


# ---------------------------------------------------------------------------
# Output budget
# ---------------------------------------------------------------------------
# Every council generator call used to pass a hardcoded max_tokens=4096,
# regardless of how much output the task actually asked for. That is fine for
# a scoring call (a small JSON object) and wrong for resume tailoring, whose
# prompt ends with "Return ONLY the tailored body" against a base body of
# 11,000-17,000 characters of LaTeX -- the model is being asked to re-emit a
# whole document, not to write a paragraph.
#
# Measured on the two base resumes in resumes/ (2026-09-28): an 11,643-char
# body needs AT LEAST 2,430 tokens to emit, counted by applying the
# cl100k/o200k pre-tokenizer split, which BPE merges never cross -- so that
# figure is a hard floor, not an estimate, and the real count is higher
# because LaTeX control sequences (\section*{, \textbf{, \item) split further.
# A 4096 cap therefore leaves little or no margin on a body that size, and
# none at all on a larger one.
#
# Reasoning models make it worse in a way character counts do not show. The
# model registry records `min_output_tokens: 3000` for groq/gpt-oss-120b and
# groq/gpt-oss-20b, and agents/model_registry.json's own _TOKENS note explains
# why: "Reasoning models consume the budget internally before emitting." A
# model that spends 2,000 tokens thinking has ~2,000 left to write with, which
# runs out around the end of Technical Skills -- exactly where the AI Eval
# Gate's two failing tailor cases stop.
_CHARS_PER_TOKEN_LATEX = 3.0

# Headroom for a model that reasons before it writes. Same figure the registry
# records as the min_output_tokens floor for the two gpt-oss entries.
_REASONING_HEADROOM_TOKENS = 3000

# Nothing in the pool is known to reject 8192, and several free endpoints do
# reject much more. A document that needs more than this still truncates --
# but `_call_provider` now says so out loud instead of returning the fragment
# as a finished answer, which is the part that was actually silent.
MAX_OUTPUT_TOKENS_CAP = 8192


def rewrite_budget(text: str, *, reasoning_headroom: int = _REASONING_HEADROOM_TOKENS) -> int:
    """Output-token budget for a task whose answer re-emits `text`.

    Deliberately generous: over-provisioning max_tokens costs nothing on a
    provider that bills actual usage, while under-provisioning silently
    truncates the document.

    Groq is the one provider where a bigger budget is not free -- it bills
    prompt_tokens + max_tokens against an 8k/minute free-tier allowance (see
    score_batch.SCORE_MAX_TOKENS's note). A tailoring prompt already carries
    the whole base resume, so it is close to that ceiling before max_tokens
    is added at all; in the 2026-09-28 eval run every Groq entry returned 429
    and Gemini served the run. Widening the budget makes an already-failing
    hop fail the same way, and Groq's own failover moves on in milliseconds.

    Never returns less than the historical 4096 default, so this can only
    widen a budget, never narrow one.
    """
    needed = int(len(text or "") / _CHARS_PER_TOKEN_LATEX) + reasoning_headroom
    return max(4096, min(needed, MAX_OUTPUT_TOKENS_CAP))


def prefer_complete(candidates: list[dict]) -> list[dict]:
    """Drop candidates the provider cut off, unless that leaves nothing.

    A truncated candidate is a partial document. Handing it to the critic
    alongside a complete one lets a fragment win on style points -- the critic
    scores prose quality and has no idea the answer stops mid-section. When
    EVERY candidate is truncated the list is returned unchanged: a partial
    answer is still better than no answer, and the caller's own validation
    (tailor_resume's required-sections gate, the eval harness's check_output)
    is what decides whether to ship it.
    """
    complete = [c for c in candidates if c and not c.get("truncated")]
    if complete and len(complete) != len(candidates):
        logger.warning(
            "[council] Discarded %d truncated candidate(s) of %d before critique",
            len(candidates) - len(complete), len(candidates),
        )
    return complete or candidates


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
            finish_reason = choice.get("finish_reason")
            if not content:
                # A reasoning model that ran out of budget looks identical to a
                # broken provider unless we say so explicitly. finish_reason
                # 'length' plus a populated `reasoning` field means the model
                # worked fine and max_tokens was simply too low.
                if finish_reason == "length" and message.get("reasoning"):
                    logger.warning(
                        "[ai] %s returned only reasoning tokens — max_tokens=%s too low "
                        "for a reasoning model, raise the budget",
                        provider["name"], max_tokens,
                    )
                else:
                    logger.warning(f"[ai] {provider['name']} returned empty content")
                return None
            note_provider_success(provider)
            # finish_reason is carried out, not dropped. The empty-content
            # branch above was the ONLY place it was ever consulted, so a
            # response cut off mid-document -- non-empty content, finish_reason
            # 'length' -- came back indistinguishable from a complete one and
            # every caller treated it as a finished answer. That is how a
            # tailored resume body that stops in the middle of Technical
            # Skills reaches the output guards as "the model dropped four
            # sections" instead of "the model was cut off": see
            # `rewrite_budget` above, and tests/unit/test_ai_truncation.py.
            truncated = finish_reason == "length"
            if truncated:
                logger.warning(
                    "[ai] %s hit max_tokens=%s mid-response (finish_reason=length, "
                    "%d chars returned) — the answer is incomplete",
                    provider["name"], max_tokens, len(content),
                )
            return {
                "content": content,
                "provider": provider["name"],
                "model": provider["model"],
                "finish_reason": finish_reason,
                "truncated": truncated,
            }
        elif resp.status_code == 429:
            logger.warning(f"[ai] {provider['name']} rate limited")
            note_provider_failure(provider, 429, resp.text)
        else:
            logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
            # The body decides scope for a 403 (moderation vs bad key), so it
            # is forwarded on every failure, not only on 429.
            note_provider_failure(provider, resp.status_code, resp.text)
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

    # Cooled-down providers go to the BACK, always. The cooldown table is the
    # only memory this chain has of what just failed; ignoring it means paying
    # a full timeout to rediscover a 429 already recorded. They are moved
    # rather than dropped, so a stale or over-long cooldown can never empty
    # the chain — worst case we end up exactly where we started.
    live = [p for p in providers if _is_available(p)]
    cooled = [p for p in providers if not _is_available(p)]

    # A/B testing: ~20% of calls shuffle the tail of the LIVE group.
    #
    # Exploration is worth a round-trip only among providers that might answer.
    # This shuffle predates the cooldown table and originally reordered the
    # whole list, which meant that once OpenRouter's account-wide
    # free-models-per-day cap tripped, one call in five promoted a provider
    # already known to be exhausted. Measured 2026-09-28 with that cap blown:
    # the unrestricted shuffle put four cooled providers ahead of four live
    # ones, and it showed up as a 1-in-7 flake in the unit suite.
    if live and random.random() < 0.2:
        tail = live[1:]
        random.shuffle(tail)
        live = [live[0]] + tail
        logger.info(f"[ab_test] shuffled: {[p['name'] for p in live[:3]]}...")

    providers = live + cooled

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
    max_tokens: int = 4096,
) -> dict:
    """Generate candidates from diverse models, pick the best by critic score.

    `max_tokens` is the per-generator output budget. It defaults to the 4096
    that used to be hardcoded in both engines, so every existing caller keeps
    its old behaviour; callers whose answer is a whole document (tailoring)
    pass `rewrite_budget(base_body)` instead. The critic is NOT sized by this
    -- it returns a short numeric verdict and keeps CRITIC_MAX_TOKENS.

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
            header_markers=header_markers, max_tokens=max_tokens,
        )
    # Legacy has no guard nodes at all -- _council_complete_legacy below never
    # imports guardrails, so `task`/`base_skills`/`base_body`/`header_markers`
    # have nothing to plug into on this path. Dropped here deliberately, not
    # forgotten: legacy is frozen pre-guardrail behaviour, kept only as the
    # COUNCIL_ENGINE=legacy escape hatch and the parity test's baseline, and
    # is not a candidate for picking up guard-policy awareness of its own.
    #
    # max_tokens IS forwarded, unlike the guard-context arguments above: it is
    # not guard-policy awareness, it is the size of the answer the caller
    # asked for.
    #
    # CORRECTED 2026-09-29. This comment used to say legacy was "the engine
    # that actually runs today", reasoning from template.yaml's CouncilEngine
    # default of "legacy". That default is overridden in samconfig.toml, so it
    # describes the template and not the stack. Measured against the deployed
    # functions:
    #
    #   naukribaba-tailor-resume          langgraph
    #   naukribaba-generate-cover-letter  langgraph
    #   naukribaba-score-batch            langgraph (never calls the council)
    #   naukribaba-post-score             None -> fell through to legacy
    #
    # Production runs the graph. Legacy is the escape hatch and the parity
    # test's baseline, nothing more. Read the stack, not the default.
    # `guard_report: None` is added here rather than at legacy's five return
    # points, and the value is the honest one: legacy has no guard nodes, so it
    # has not measured anything, and `None` means exactly that. Flattening it
    # to an empty report would claim a clean verdict from an engine that never
    # looked -- the same lie as a status that cannot fail. The KEY is present
    # because test_return_shape_matches_legacy_contract asserts an exact set on
    # both engines: a key on one and not the other is how post_score silently
    # ran guard-free for weeks.
    return {
        **_council_complete_legacy(
            prompt, system, task_description, n_generators, temperature,
            max_tokens=max_tokens,
        ),
        "guard_report": None,
    }


def _council_complete_legacy(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
    max_tokens: int = 4096,
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
        result = _call_provider(gen, prompt, system, temperature, max_tokens=max_tokens)
        if result and result.get("content"):
            candidates.append(result)
            used_families.add(_model_family(gen["model"]))
        else:
            # Primary provider failed — try others from different families
            for fallback in all_providers:
                fb_fam = _model_family(fallback["model"])
                if fb_fam in used_families or fb_fam == _model_family(gen["model"]):
                    continue
                result = _call_provider(fallback, prompt, system, temperature, max_tokens=max_tokens)
                if result and result.get("content"):
                    candidates.append(result)
                    used_families.add(fb_fam)
                    logger.info(f"[council] Generator fallback: {fallback['name']} succeeded")
                    break

    if not candidates:
        raise RuntimeError("Council: all generators failed")
    candidates = prefer_complete(candidates)
    if len(candidates) == 1:
        logger.info("[council] outcome=single_candidate — returning without critique")
        return {**candidates[0], "critique_outcome": "single_candidate"}

    # Step 3: Select critic from a family that neither was assigned to generate
    # nor actually produced a candidate. The second set matters: a generator's
    # fallback can succeed from a third family, which then wrote a candidate.
    #
    # NEVER relaxes -- the same contract as agents/providers.select_critics
    # (5e9794d), stated in 17b3b48: a critic from the family that generated is
    # not an independent reviewer, which is the entire purpose of the slot.
    # This used to fall back to `_select_diverse_providers(all_providers, n=1)`
    # with no exclusion, so a degraded pool had a generator judge its own
    # family's work and the run was recorded as "adjudicated".
    gen_families = ({_model_family(g["model"]) for g in generators}
                    | {_model_family(c["model"]) for c in candidates})
    critics = _select_diverse_providers(all_providers, n=1, exclude_families=gen_families)
    if not critics:
        logger.warning(
            "[council] outcome=no_critic_family — every live family is already "
            "a generator (%s), so no cross-family critic is available; "
            "returning candidate 1 unadjudicated",
            ", ".join(sorted(f for f in gen_families if f)),
        )
        return {**candidates[0], "critique_outcome": "no_critic_family"}

    critic_provider = critics[0]
    logger.info(f"[council] Critic: {critic_provider['name']}:{critic_provider['model']}")

    # Step 4: Build critique prompt with numeric scoring
    critique_prompt = build_critique_prompt(candidates, task_description)

    try:
        critique = _call_provider(
            critic_provider, critique_prompt,
            system=CRITIQUE_SYSTEM,
            temperature=0, max_tokens=critic_budget(critic_provider),
        )
        if not critique:
            logger.warning("[council] outcome=critic_call_failed — critic %s "
                           "returned nothing; candidate 1 unadjudicated",
                           critic_provider["name"])
            return {**candidates[0], "critique_outcome": "critic_call_failed"}

        scores = _parse_critic_scores(critique["content"], len(candidates))
        if scores:
            best_idx = max(range(len(scores)), key=lambda i: scores[i])
            winner = candidates[best_idx]
            logger.info(
                f"[council] Scores: {scores}, Winner: candidate {best_idx + 1} "
                f"({winner['provider']}:{winner['model']}) score={scores[best_idx]}"
            )
            return {**winner, "critique_outcome": "adjudicated", "scores": scores}
        else:
            logger.warning("[council] outcome=critic_unparseable — critic %s "
                           "answered in an unreadable shape; candidate 1 "
                           "unadjudicated. Raw: %r",
                           critic_provider["name"], critique["content"][:300])
            return {**candidates[0], "critique_outcome": "critic_unparseable"}
    except Exception as e:
        logger.warning("[council] outcome=critic_call_failed — critique raised "
                       "(%s); candidate 1 unadjudicated", e)
        return {**candidates[0], "critique_outcome": "critic_call_failed"}


# ---------------------------------------------------------------------------
# ai_complete_cached — with Supabase cache
# ---------------------------------------------------------------------------

def json_parses(text: str) -> bool:
    """True if `text` is JSON, allowing one surrounding markdown code fence.

    The `validate` for ai_complete_cached callers whose answer must be JSON.
    Strips fences the way score_batch.score_single_job does before parsing,
    so a fenced answer the caller can read is not rejected here.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        t = parts[1] if len(parts) >= 2 else t
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        json.loads(t)
    except (ValueError, TypeError):
        return False
    return True


def _passes(validate: Callable[[str], bool] | None, content: str) -> bool:
    """A validator that raises is a rejection: never cache on a crash."""
    if validate is None:
        return True
    try:
        return validate(content) is True
    except Exception as e:  # noqa: BLE001 -- a broken validator must not break the call
        logger.warning(f"[ai] cache validator raised ({e!r}); treating as invalid")
        return False


def _cache_key(system: str, prompt: str, temperature: float, max_tokens: int) -> str:
    """Everything that shapes the answer is in the key.

    It used to be md5(system|prompt), so a temperature-0 scoring answer was
    replayed to a temperature-0.7 caller and a 1024-token answer to a caller
    who asked for 8192. Changing the format orphans existing entries once;
    they expire on their own 72h TTL.
    """
    raw = f"{system}|{prompt}|temperature={temperature}|max_tokens={max_tokens}"
    return hashlib.md5(raw.encode()).hexdigest()


def ai_complete_cached(
    prompt: str,
    system: str = "",
    cache_hours: int = 72,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    skip_cache: bool = False,
    validate: Callable[[str], bool] | None = None,
) -> dict:
    """AI complete with Supabase cache. Returns dict with content, provider, model.

    skip_cache=True neither reads nor writes the cache. It exists for repeat
    sampling: score_single_job_deterministic takes the median of num_calls
    independent calls, and a cache hit makes those calls identical, so the
    median of three becomes the median of one answer returned three times —
    the variance the median exists to dampen is invisible to it.

    The WRITE is skipped too, not just the read. Otherwise call 1 would
    populate the key that calls 2 and 3 then read, rebuilding the collapse this
    avoids, and leaving one arbitrary sample in the shared cache for the batch
    pipeline to pick up afterwards.

    Default stays False: the batch pipeline scores ~58 jobs a run against an 8k
    tokens/minute Groq ceiling that is already the bottleneck.

    `validate`, when given, decides whether an answer is fit to STORE. It used
    to be stored before the caller parsed it, so one unparseable scoring
    answer was replayed for 72h and the job could not be re-scored until the
    entry expired. Now an answer is cached only if `validate(content)` is
    True, and a cached entry that fails it is ignored and re-asked, which also
    flushes entries poisoned before this existed. The answer is still
    RETURNED either way: the caller may be able to salvage it.
    """
    cache_key = _cache_key(system, prompt, temperature, max_tokens)
    db = get_supabase()

    if not skip_cache:
        cached = db.table("ai_cache").select("response, provider, model") \
            .eq("cache_key", cache_key) \
            .gte("expires_at", datetime.utcnow().isoformat()).execute()
        if cached.data:
            if _passes(validate, cached.data[0]["response"]):
                return {
                    "content": cached.data[0]["response"],
                    "provider": cached.data[0].get("provider", "cache"),
                    "model": cached.data[0].get("model", "cache"),
                }
            logger.warning(
                "[ai] cached response for cache_key=%s fails validation — "
                "ignoring it and asking again", cache_key,
            )

    result = ai_complete(prompt, system, temperature=temperature, max_tokens=max_tokens)

    if result.get("truncated"):
        # A cut-off answer must never become the answer for the next 72
        # hours. The cache is a cost optimisation; persisting a fragment
        # turns one provider hiccup into three days of identical failures
        # that no re-run can shake off, and `cache_key` is md5(system|prompt)
        # so every caller with the same prompt inherits it.
        #
        # Checked ahead of `skip_cache` rather than folded into it: the two
        # suppress the same write for unrelated reasons (skip_cache is the
        # determinism-sampling caller opting out; this is the answer being
        # unfit to store), and only this one is worth a log line.
        logger.warning(
            "[ai] not caching a truncated response for cache_key=%s "
            "(max_tokens=%s) — a fragment must not be replayed for %sh",
            cache_key, max_tokens, cache_hours,
        )
        return result

    if not _passes(validate, result.get("content") or ""):
        logger.warning(
            "[ai] not caching a response that failed validation for "
            "cache_key=%s — it would be replayed for %sh", cache_key, cache_hours,
        )
        return result

    if not skip_cache:
        db.table("ai_cache").upsert({
            "cache_key": cache_key,
            "response": result["content"],
            "provider": result["provider"],
            "model": result["model"],
            "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
        }, on_conflict="cache_key").execute()

    return result
