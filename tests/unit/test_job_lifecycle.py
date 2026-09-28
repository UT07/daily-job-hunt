"""Age-based job lifecycle.

Driven by the 2026-09-28 stale-nudge email, which opened with a 144-day-old
role under the heading "Time to apply!". `is_expired` only catches postings
whose apply_url 404s; nothing aged a live-but-ancient listing out.

Owner's rule: active under 14 days, stale 14-30, gone after 30 -- unless the
user applied to it, in which case it stays regardless of age.
"""
from datetime import datetime, timedelta, timezone

import pytest

from shared.job_lifecycle import (
    ACTIVE, ARCHIVED, ARCHIVE_AFTER_DAYS, STALE, STALE_AFTER_DAYS,
    age_days, is_engaged, lifecycle_state,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def seen(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.mark.parametrize("days,expected", [
    (0, ACTIVE), (7, ACTIVE), (13, ACTIVE),
    (14, STALE), (20, STALE), (29, STALE),
    (30, ARCHIVED), (144, ARCHIVED),
])
def test_thresholds(days, expected):
    assert lifecycle_state(seen(days), "New", now=NOW) == expected


def test_boundaries_are_where_the_owner_said():
    assert STALE_AFTER_DAYS == 14
    assert ARCHIVE_AFTER_DAYS == 30


@pytest.mark.parametrize("status", ["Applied", "Interviewing", "Offer", "Rejected", "Withdrawn"])
def test_engaged_jobs_never_age_out(status):
    """'unless I applied to it' -- Rejected and Withdrawn imply an application."""
    assert lifecycle_state(seen(365), status, now=NOW) == ACTIVE
    assert is_engaged(status) is True


@pytest.mark.parametrize("status", ["New", "scored", "ready", "failed", None, ""])
def test_unengaged_statuses_do_age_out(status):
    assert lifecycle_state(seen(365), status, now=NOW) == ARCHIVED
    assert is_engaged(status) is False


def test_missing_timestamp_is_never_archived():
    """Archiving on a bad timestamp would be data loss wearing a feature's hat."""
    assert lifecycle_state(None, "New", now=NOW) == ACTIVE
    assert lifecycle_state("not-a-date", "New", now=NOW) == ACTIVE
    assert age_days(None) is None


def test_naive_timestamps_are_treated_as_utc():
    naive = (NOW - timedelta(days=40)).replace(tzinfo=None).isoformat()
    assert lifecycle_state(naive, "New", now=NOW) == ARCHIVED


def test_the_actual_email_offenders():
    """Every role in the 2026-09-28 nudge, by its reported age."""
    for reported_age in (126, 119, 118, 111, 108, 144, 122, 110, 32):
        assert lifecycle_state(seen(reported_age), "New", now=NOW) == ARCHIVED
    # the one borderline entry in that email
    assert lifecycle_state(seen(32), "New", now=NOW) == ARCHIVED
