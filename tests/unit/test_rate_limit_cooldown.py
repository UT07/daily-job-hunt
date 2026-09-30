"""429 means two different things, and treating them alike wastes the day.

OpenRouter's free pool sends HTTP 429 both for a short per-minute throttle and
for the account-wide per-day quota ("Rate limit exceeded: free-models-per-day").
The second does not clear until UTC midnight. Measured 2026-09-28: that cap is
50 requests/day without credits on the account, and it takes all eight
OpenRouter models down together.

A flat 30-minute cooldown therefore re-probed a provably dead account roughly
48 times a day. Cooling until midnight on EVERY 429 is the opposite error: a
transient throttle would disable a healthy provider for up to 24 hours, and
that provider may be the only live failover the council has.

The response body is the only thing that distinguishes them, so the body is
what decides.
"""
import sys
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    ai_helper._reset_cooldowns()
    yield
    ai_helper._reset_cooldowns()


DAILY_BODY = (
    '{"error":{"message":"Rate limit exceeded: free-models-per-day. '
    'Add 10 credits to unlock 1000 free model requests per day","code":429}}'
)
MINUTE_BODY = '{"error":{"message":"Rate limit exceeded: 20 requests per minute","code":429}}'


def _seconds_to_utc_midnight():
    now = datetime.now(UTC)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds()


def test_daily_cap_cools_until_utc_midnight():
    """The daily quota resets at UTC midnight, so that is when to retry."""
    secs = ai_helper._rate_limit_cooldown_seconds("openrouter", DAILY_BODY)
    expected = _seconds_to_utc_midnight()
    assert abs(secs - expected) < 120, (
        f"expected ~{expected:.0f}s (to UTC midnight), got {secs}. "
        "A flat cooldown re-probes a dead account all day."
    )


def test_per_minute_throttle_keeps_the_short_cooldown():
    """A minute-throttle is a healthy provider; do not bench it for the day."""
    secs = ai_helper._rate_limit_cooldown_seconds("openrouter", MINUTE_BODY)
    assert secs == ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"], (
        f"a transient throttle was treated as a daily cap ({secs}s) — that "
        "disables a healthy provider for up to 24h"
    )


def test_empty_body_is_not_assumed_to_be_a_daily_cap():
    """Absent evidence, take the conservative (short) option."""
    assert ai_helper._rate_limit_cooldown_seconds("openrouter", "") == (
        ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"]
    )


def test_unknown_account_still_gets_a_default():
    """A provider with no tuned constant must not crash or cool forever."""
    assert ai_helper._rate_limit_cooldown_seconds("brand-new-provider", "") == 300


def test_daily_detection_is_not_provider_specific():
    """Any provider that names a per-day cap gets the same treatment.

    The wording is OpenRouter's today, but the rule is about the KIND of limit,
    not about who sent it. Hard-coding the account would silently mistreat the
    next provider to adopt the same phrasing.
    """
    secs = ai_helper._rate_limit_cooldown_seconds("groq", DAILY_BODY)
    assert secs > ai_helper._RATE_LIMIT_COOLDOWN_S["groq"]


def test_429_records_the_body_derived_cooldown_end_to_end():
    """note_provider_failure must actually route the body into the policy."""
    provider = {"name": "openrouter/x", "model": "a/b:free",
                "key_param": "/naukribaba/OPENROUTER_API_KEY"}
    import time

    ai_helper.note_provider_failure(provider, 429, DAILY_BODY)
    assert not ai_helper._is_available(provider), "provider should be cooled"

    # The daily cooldown runs until UTC MIDNIGHT, so its length depends on the
    # time of day. Probing at a fixed `now + flat_window + 60` asserted the
    # provider was still cooled at a moment that is past midnight whenever the
    # test runs within that window of it — i.e. this failed for roughly the
    # last 31 minutes of every UTC day. Caught at 23:48 UTC on 2026-09-29,
    # when it failed on clean main and looked like a regression from unrelated
    # work.
    #
    # Assert the property instead of a wall-clock instant: a body naming the
    # daily quota must produce a LONGER cooldown than the flat per-account
    # window, whatever the hour. Near midnight that is legitimately shorter, so
    # compare against what the same body would yield, not a constant.
    flat = ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"]
    daily = ai_helper._rate_limit_cooldown_seconds("openrouter", DAILY_BODY)
    transient = ai_helper._rate_limit_cooldown_seconds("openrouter", "429 too many requests")
    assert transient == flat, (
        f"an unrecognised body should take the flat window, got {transient}"
    )
    if daily > flat:
        # The usual case: still hours from midnight.
        future = time.time() + flat + 60
        assert not ai_helper._is_available(provider, now=future), (
            "cooldown expired after the flat window — the body was ignored"
        )
    else:
        # Within `flat` seconds of UTC midnight the daily cooldown is genuinely
        # the shorter of the two. The body was still read; there is just less
        # of the day left to wait.
        assert daily <= flat
        assert not ai_helper._is_available(provider, now=time.time() + daily - 5)
