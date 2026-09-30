"""Unit test fixtures — everything mocked."""
import sys
import time as real_time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def patch_boto3_ssm():
    """Prevent any real AWS calls in unit tests."""
    with patch("boto3.client") as mock_client:
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "mock-value"}
        }
        mock_client.return_value = mock_ssm
        yield mock_client


@pytest.fixture
def pinned_clock(monkeypatch):
    """Pin every clock `ai_helper` reads to one chosen instant.

    The rate-limit cooldown for a per-day quota runs until the next UTC
    midnight, so its LENGTH is a function of the time of day — 46,800s at
    11:00, 240s at 23:56, and a floored 60s in the last minute. Any assertion
    about that window which reads the wall clock is therefore testing a
    different thing on every run, and has now failed three times on clean main
    for that reason alone:

      * `test_429_records_the_body_derived_cooldown_end_to_end` probed a fixed
        `now + 1800 + 60`, so it failed for the last ~31 minutes of every UTC
        day (caught 23:48 UTC, 2026-09-29, where it read as a regression from
        unrelated work);
      * `test_daily_detection_is_not_provider_specific` and
        `test_a_cerebras_429_naming_a_daily_cap_still_cools_until_midnight`
        both assert the window exceeds a 90s flat one, which the 60s floor
        makes false for the last 90 seconds of every UTC day (measured
        2026-09-30: passes at 23:58:00, fails at 23:58:30).

    Both clocks are pinned together or the arithmetic is incoherent:
    `_seconds_to_utc_midnight` asks `datetime.now(UTC)` how much of the day is
    left, while `_cool_down` stamps the expiry with `time.time()`. Pinning one
    and not the other yields an expiry belonging to neither.

    This lives in conftest rather than in each test module so there is ONE
    clock double. Two copies drift, and a double that is subtly wrong fails the
    way the bug fails (CLAUDE.md rule 6).

    Returns the pinned epoch, so a test can say what must be true `n` seconds
    from it without consulting a clock of its own.
    """
    # Imported here rather than at module scope: this conftest loads for every
    # unit test, and ai_helper pulls in httpx and boto3.
    if "lambdas/pipeline" not in sys.path:
        sys.path.insert(0, "lambdas/pipeline")
    import ai_helper

    def _pin(at_utc: str) -> float:
        at = datetime.fromisoformat(at_utc).replace(tzinfo=UTC)
        epoch = at.timestamp()

        class _PinnedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return at if tz is not None else at.replace(tzinfo=None)

            @classmethod
            def utcnow(cls):
                return at.replace(tzinfo=None)

        class _PinnedTime:
            """`time.time()` is frozen; everything else is the real module."""

            @staticmethod
            def time() -> float:
                return epoch

            def __getattr__(self, name):
                return getattr(real_time, name)

        monkeypatch.setattr(ai_helper, "datetime", _PinnedDatetime)
        monkeypatch.setattr(ai_helper, "time", _PinnedTime())
        return epoch

    return _pin
