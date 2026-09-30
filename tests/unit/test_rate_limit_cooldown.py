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

EVERY TEST HERE PINS THE CLOCK, AND THAT IS THE POINT

The daily cooldown runs until the next UTC midnight, so its LENGTH is a
function of the time of day: 46,800s at 11:00, 240s at 23:56. Any test that
reads the wall clock is therefore testing a different thing on every run, and
this file has been bitten twice:

  * The end-to-end test probed a fixed `now + flat_window + 60` and so asserted
    the account was still cooling at an instant past midnight. It failed on
    clean main for the last ~31 minutes of every UTC day — caught at 23:48 UTC
    on 2026-09-29, where it read as a regression from unrelated work.
  * `test_daily_detection_is_not_provider_specific` asserted `secs > 90`, which
    the 60s floor makes false for the last 90 seconds of every UTC day.

The first was then "fixed" by branching on the wall clock — probing the long
window when there was time for it, and a short one otherwise. That removed the
failure and the coverage with it: inside the last 1,800 seconds of the day the
short probe passes whether or not `note_provider_failure` read the body at all,
so for 31 minutes a day the test reported success over the exact bug it exists
to catch (CLAUDE.md rule 2). Measured 2026-09-30 by feeding it a
`note_provider_failure` that ignores the body: PASS at 23:48 and 23:56.

So the clock is pinned instead of consulted, via the `pinned_clock` fixture in
`tests/unit/conftest.py`. Each case names an instant and what must be true at
it, the near-midnight instants included, and the whole file asserts the same
things at 03:00 as at 23:59.
"""
import sys

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

PROVIDER = {"name": "openrouter/x", "model": "a/b:free",
            "key_param": "/naukribaba/OPENROUTER_API_KEY"}


# (instant in UTC, seconds from it to the next UTC midnight).
#
# The second column is computed by hand, not by re-running the helper's own
# arithmetic: a test that mirrors the implementation is wrong in precisely the
# ways the implementation is wrong, and passes anyway. The instants straddle the
# flat 1,800s OpenRouter window deliberately, because that is where the policy
# inverts and where both historical failures lived.
PINNED_CASES = [
    ("2026-09-29T00:04:00", 86_160),  # just after a reset — nearly a full day
    ("2026-09-29T11:00:00", 46_800),  # mid-day, the case that always passed
    ("2026-09-29T23:29:00", 1_860),   # exactly the old probe's offset: the
                                      # crossover, where the two windows meet
    ("2026-09-29T23:48:00", 720),     # the instant it failed on clean main
    ("2026-09-29T23:56:00", 240),     # measured 2026-09-29
    ("2026-09-29T23:59:30", 60),      # the 60s floor, which overshoots midnight
]


@pytest.mark.parametrize("at_utc, expected", PINNED_CASES)
def test_daily_cap_cools_until_utc_midnight(pinned_clock, at_utc, expected):
    """The daily quota resets at UTC midnight, so that is when to retry.

    The last case pins the 60s floor: 30 seconds before midnight the helper
    returns 60, not 30, deliberately overshooting the reset so a call landing a
    hair early still backs off.
    """
    pinned_clock(at_utc)
    secs = ai_helper._rate_limit_cooldown_seconds("openrouter", DAILY_BODY)
    assert secs == expected, (
        f"at {at_utc}Z expected {expected}s (to UTC midnight), got {secs}. "
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


@pytest.mark.parametrize("at_utc, _to_midnight", PINNED_CASES)
def test_unrecognised_body_takes_the_flat_window_at_any_hour(pinned_clock, at_utc, _to_midnight):
    """It is the body that decides, not the hour.

    Pinned across the day because the two windows cross near midnight: a test
    run only at mid-day cannot tell "took the flat window" from "took the daily
    one" once the daily one has shrunk below it.
    """
    pinned_clock(at_utc)
    secs = ai_helper._rate_limit_cooldown_seconds("openrouter", "429 too many requests")
    assert secs == ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"], (
        f"an unrecognised body took {secs}s at {at_utc}Z — the flat window is "
        f"{ai_helper._RATE_LIMIT_COOLDOWN_S['openrouter']}s"
    )


def test_unknown_account_still_gets_a_default():
    """A provider with no tuned constant must not crash or cool forever."""
    assert ai_helper._rate_limit_cooldown_seconds("brand-new-provider", "") == 300


def test_daily_detection_is_not_provider_specific(pinned_clock):
    """Any provider that names a per-day cap gets the same treatment.

    The wording is OpenRouter's today, but the rule is about the KIND of limit,
    not about who sent it. Hard-coding the account would silently mistreat the
    next provider to adopt the same phrasing.

    Pinned mid-day because Groq's flat window is 90s and the daily cooldown
    floors at 60s: unpinned, this assertion is false for the last 90 seconds of
    every UTC day, which is the same defect the end-to-end test had.
    """
    pinned_clock("2026-09-29T11:00:00")
    secs = ai_helper._rate_limit_cooldown_seconds("groq", DAILY_BODY)
    assert secs > ai_helper._RATE_LIMIT_COOLDOWN_S["groq"]


@pytest.mark.parametrize("at_utc, to_midnight", PINNED_CASES)
def test_429_records_the_body_derived_cooldown_end_to_end(pinned_clock, at_utc, to_midnight):
    """note_provider_failure must actually route the body into the policy.

    Asserted by bracketing the recorded expiry to the second, which is what
    tells the two policies apart at EVERY hour — the property a single probe at
    a fixed offset does not have:

      * hours from midnight the daily window is the LONGER one, so a flat expiry
        would already have cleared at `to_midnight - 1`;
      * inside the last 1,800 seconds of the day it is the SHORTER one, so a
        flat expiry would still be cooling at `to_midnight + 1`.

    Either way an ignored body moves the expiry off the boundary and one of the
    two probes fails. Confirmed 2026-09-30 against a `note_provider_failure`
    stubbed to ignore the body: every case fails, including 23:48 and 23:56,
    which the previous wall-clock branch passed.
    """
    epoch = pinned_clock(at_utc)

    ai_helper.note_provider_failure(PROVIDER, 429, DAILY_BODY)

    assert not ai_helper._is_available(PROVIDER), "provider should be cooled"
    assert not ai_helper._is_available(PROVIDER, now=epoch + to_midnight - 1), (
        f"at {at_utc}Z the account cleared before UTC midnight ({to_midnight}s "
        "away) — the body was ignored and the flat window applied"
    )
    assert ai_helper._is_available(PROVIDER, now=epoch + to_midnight + 1), (
        f"at {at_utc}Z the account was still cooling past UTC midnight "
        f"({to_midnight}s away) — the quota has reset, so it must be retried"
    )


def test_daily_body_outcools_the_flat_window(pinned_clock):
    """The reason for reading the body at all.

    A per-day cap must bench the account for longer than a per-minute throttle
    would — true at every instant except the last 1,800 seconds of the UTC day,
    where the daily window is legitimately shorter because there is less of the
    day left to wait. That exception is asserted here, from pinned instants,
    rather than discovered at runtime from the wall clock.
    """
    flat = ai_helper._RATE_LIMIT_COOLDOWN_S["openrouter"]
    longer, shorter = [], []

    for at_utc, to_midnight in PINNED_CASES:
        pinned_clock(at_utc)
        daily = ai_helper._rate_limit_cooldown_seconds("openrouter", DAILY_BODY)
        (longer if to_midnight > flat else shorter).append((at_utc, daily))

    assert longer and shorter, (
        "the cases no longer straddle the flat window, so this test cannot see "
        "the inversion it exists to pin"
    )
    for at_utc, daily in longer:
        assert daily > flat, (
            f"at {at_utc}Z a daily cap cooled for {daily}s, no longer than the "
            f"flat {flat}s window — the whole point of reading the body is lost"
        )
    for at_utc, daily in shorter:
        assert 60 <= daily <= flat, (
            f"at {at_utc}Z, near midnight, expected a window no longer than the "
            f"flat {flat}s and no shorter than the 60s floor, got {daily}s"
        )
