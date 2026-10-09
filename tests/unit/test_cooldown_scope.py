"""Cooldown SCOPE follows what each vendor documents, not one rule for all.

A 429 used to cool the whole ACCOUNT for every provider. That is right for
OpenRouter and wrong for Groq and Gemini, and the difference is what collapsed
the council over the 212-résumé batch of 2026-10-07 (commit 17b3b48): Groq
took 41 account-wide cooldowns, so one model's throttle benched its siblings,
and the live pool fell to Gemini alone.

What the vendors say, read 2026-10-08:

  OpenRouter  docs/api-reference/limits: free-model limits apply per ACCOUNT
              ("free_model_daily_requests" is an account counter; 50/day
              under 10 credits). -> 429 stays account-scoped.
  Groq        docs/rate-limits: "Rate limits apply at the organization level,
              not individual users", and the limits table is keyed by MODEL
              ID (openai/gpt-oss-120b: 30 RPM / 1K RPD / 8K TPM / 200K TPD;
              qwen/qwen3.8-27b its own row). Its 429 body names the model:
              "Rate limit reached for model `...` in organization `...`".
              -> each model is its own bucket within the org: model-scoped.
  Gemini      ai.google.dev/gemini-api/docs/rate-limits: "Rate limits are
              applied per project, not per API key", "Limits vary depending
              on the specific model being used", and "Requests per day (RPD)
              quotas reset at midnight Pacific time". Its quota ids say so
              outright: GenerateRequestsPerDayPerProjectPerModel. -> model.
  Cerebras    support/rate-limits: "Rate limits apply at the organization
              level, not the user level, and vary based on the model." The
              same ambiguity as Groq's sentence without a per-model table or
              error format to settle it, and only one Cerebras model is in the
              pool, so it stays account-scoped until measured.

401/402/403 are a missing, invalid or unfunded key (OpenRouter documents 401
"invalid credentials", 402 "insufficient credits"). That is a property of the
ACCOUNT, so every model on it is equally dead, and it is not transient. The
exception is OpenRouter's documented 403 "moderation flag": that is about one
request, and benching the account for hours over one flagged prompt would be
the opposite error.
"""
import logging
import sys

import httpx
import pytest

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    ai_helper._reset_cooldowns()
    yield
    ai_helper._reset_cooldowns()


GROQ_120B = {"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}
GROQ_QWEN = {"name": "groq/qwen3.8-27b", "model": "qwen/qwen3.8-27b"}
GEM_A = {"name": "gemini/gemini-3.5-flash-lite", "model": "gemini-3.5-flash-lite"}
GEM_B = {"name": "gemini/gemini-3.5-flash", "model": "gemini-3.5-flash"}
OR_A = {"name": "openrouter/a", "model": "x/a:free"}
OR_B = {"name": "openrouter/b", "model": "x/b:free"}
CER_A = {"name": "cerebras/gpt-oss-120b", "model": "gpt-oss-120b"}
CER_B = {"name": "cerebras/other", "model": "other"}

GROQ_TPM_BODY = (
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in '
    'organization `org_x` service tier `on_demand` on tokens per minute (TPM): '
    'Limit 8000, Used 7900, Requested 900.","type":"tokens","code":"rate_limit_exceeded"}}'
)
GEMINI_DAILY_BODY = (
    '[{"error":{"code":429,"message":"You exceeded your current quota.","status":'
    '"RESOURCE_EXHAUSTED","details":[{"@type":"type.googleapis.com/google.rpc.'
    'QuotaFailure","violations":[{"quotaMetric":"generativelanguage.googleapis.com/'
    'generate_content_free_tier_requests","quotaId":'
    '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}]'
)


# -- 429 scope --------------------------------------------------------------

def test_a_groq_429_cools_that_model_and_not_its_siblings():
    ai_helper.note_provider_failure(GROQ_120B, 429, GROQ_TPM_BODY)
    assert ai_helper._is_available(GROQ_120B) is False
    assert ai_helper._is_available(GROQ_QWEN) is True, (
        "Groq meters each model separately (per-model rows in its limits "
        "table); one model's throttle must not bench the others"
    )


def test_a_gemini_429_cools_that_model_and_not_its_siblings():
    ai_helper.note_provider_failure(GEM_A, 429, "")
    assert ai_helper._is_available(GEM_A) is False
    assert ai_helper._is_available(GEM_B) is True


@pytest.mark.parametrize("a,b", [(OR_A, OR_B), (CER_A, CER_B)], ids=["openrouter", "cerebras"])
def test_account_scoped_providers_still_cool_the_whole_account(a, b):
    """Control: the narrowing is per vendor, not a blanket change."""
    ai_helper.note_provider_failure(a, 429, "")
    assert ai_helper._is_available(a) is False
    assert ai_helper._is_available(b) is False


# -- per-day detection -------------------------------------------------------

def test_gemini_perday_quota_id_is_recognised_as_daily(pinned_clock):
    """"PerDay" has no separator, so none of "per-day"/"per day" matched it
    and a project's exhausted daily quota got the 300s default — re-probed
    ~250 times before it reset."""
    pinned_clock("2026-10-08T11:00:00")
    assert ai_helper._rate_limit_cooldown_seconds("gemini", GEMINI_DAILY_BODY) > 3600


@pytest.mark.parametrize("body", ["RATE LIMIT: 1000 REQUESTS PER DAY", "Free-Models-Per-Day"])
def test_daily_markers_match_case_insensitively(pinned_clock, body):
    pinned_clock("2026-10-08T11:00:00")
    assert ai_helper._rate_limit_cooldown_seconds("openrouter", body) > 3600


# Hand-computed, not derived from the helper. Gemini's RPD resets at midnight
# PACIFIC: 07:00Z under PDT (UTC-7), 08:00Z under PST (UTC-8).
@pytest.mark.parametrize("at_utc,expected", [
    ("2026-10-08T11:00:00", 20 * 3600),   # PDT: next 07:00Z is 20h away
    ("2026-01-15T11:00:00", 21 * 3600),   # PST: next 08:00Z is 21h away
    ("2026-10-08T06:30:00", 30 * 60),     # PDT, half an hour before reset
])
def test_gemini_daily_quota_cools_until_pacific_midnight(pinned_clock, at_utc, expected):
    pinned_clock(at_utc)
    assert ai_helper._rate_limit_cooldown_seconds("gemini", GEMINI_DAILY_BODY) == expected


def test_other_daily_quotas_still_reset_at_utc_midnight(pinned_clock):
    """Control: only Gemini documents a Pacific reset."""
    pinned_clock("2026-10-08T11:00:00")
    assert ai_helper._rate_limit_cooldown_seconds("openrouter", "free-models-per-day") == 13 * 3600


# -- credential failures -----------------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_credential_failure_benches_the_whole_account_for_hours(pinned_clock, caplog, status):
    epoch = pinned_clock("2026-10-08T11:00:00")
    with caplog.at_level(logging.ERROR):
        ai_helper.note_provider_failure(GROQ_120B, status, '{"error":"invalid api key"}')
    assert ai_helper._is_available(GROQ_QWEN) is False, "every model on a dead key is dead"
    assert ai_helper._is_available(GROQ_120B, now=epoch + 3 * 3600) is False, (
        "a bad or unfunded key does not fix itself in two minutes"
    )
    assert any(r.levelno >= logging.ERROR and "groq" in r.getMessage() for r in caplog.records), (
        "a credential failure must be logged loudly, not at INFO"
    )


def test_a_moderation_403_cools_only_the_model_briefly(pinned_clock):
    """OpenRouter documents 403 as 'insufficient permissions, guardrail block,
    or moderation flag'. A flagged prompt is about the request, not the key."""
    epoch = pinned_clock("2026-10-08T11:00:00")
    ai_helper.note_provider_failure(OR_A, 403, '{"error":{"message":"Input was flagged by moderation"}}')
    assert ai_helper._is_available(OR_B) is True
    assert ai_helper._is_available(OR_A, now=epoch + 600) is True


def test_a_5xx_is_still_a_short_model_cooldown(pinned_clock):
    """Control: the transient branch is unchanged."""
    epoch = pinned_clock("2026-10-08T11:00:00")
    ai_helper.note_provider_failure(GROQ_120B, 503, "")
    assert ai_helper._is_available(GROQ_QWEN) is True
    assert ai_helper._is_available(GROQ_120B, now=epoch + 600) is True


# -- end to end through _call_provider --------------------------------------

def test_call_provider_forwards_the_body_of_a_non_429_failure(monkeypatch):
    """Scope decisions read the body, so _call_provider must pass it on every
    failure, not only on 429. A real httpx.Response, not a MagicMock."""
    monkeypatch.setattr(ai_helper, "get_param", lambda _n: "k")
    resp = httpx.Response(403, text='{"error":{"message":"Input was flagged by moderation"}}')
    monkeypatch.setattr(ai_helper.httpx, "post", lambda *a, **kw: resp)
    provider = {**OR_A, "url": "https://x", "key_param": "/k"}
    sibling = {**OR_B, "url": "https://x", "key_param": "/k"}

    assert ai_helper._call_provider(provider, "p") is None
    assert ai_helper._is_available(sibling) is True, "moderation 403 benched the account"


def test_call_provider_routes_a_groq_429_to_model_scope(monkeypatch):
    monkeypatch.setattr(ai_helper, "get_param", lambda _n: "k")
    monkeypatch.setattr(ai_helper.httpx, "post", lambda *a, **kw: httpx.Response(429, text=GROQ_TPM_BODY))
    provider = {**GROQ_120B, "url": "https://x", "key_param": "/k"}

    assert ai_helper._call_provider(provider, "p") is None
    assert ai_helper._is_available(GROQ_120B) is False
    assert ai_helper._is_available(GROQ_QWEN) is True
