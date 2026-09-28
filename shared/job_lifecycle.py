"""Age-based job lifecycle.

`is_expired` means one narrow thing: the posting's apply_url returned 404/410
when check_expiry last probed it. A listing that quietly stays up forever never
ages out under that rule, which is how the 2026-09-28 stale-nudge email opened
with a 144-day-old role under the heading "Time to apply!".

Age is a separate axis from liveness, so it gets its own vocabulary:

    active   (< 14 days)  — the working dashboard
    stale    (14-30 days) — past/outdated section, still reachable
    archived (>= 30 days) — off the dashboard

Jobs the user actually engaged with are exempt at every threshold: an
application sent three months ago is history, not clutter, and must not vanish.
"""
from datetime import datetime, timedelta, timezone

STALE_AFTER_DAYS = 14
ARCHIVE_AFTER_DAYS = 30

ACTIVE = "active"
STALE = "stale"
ARCHIVED = "archived"

# Statuses meaning the user engaged with this posting. Withdrawn and Rejected
# both imply an application was sent, so they are exempt too -- the owner's
# rule is "unless I applied to it", and those are outcomes of applying.
ENGAGED_STATUSES = frozenset({
    "Applied", "Interviewing", "Interviewed", "Offer", "Accepted",
    "Rejected", "Withdrawn",
})


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def cutoff_iso(days: int, now=None) -> str:
    """ISO timestamp `days` in the past, for use as a PostgREST bound."""
    return (_now(now) - timedelta(days=days)).isoformat()


def age_days(first_seen, now=None) -> int | None:
    """Whole days since `first_seen`. None when it is missing or unparseable.

    A row with no usable first_seen is deliberately never aged out: silently
    archiving rows because of a bad timestamp would be a data-loss bug wearing
    a feature's clothes.
    """
    if not first_seen:
        return None
    if isinstance(first_seen, datetime):
        seen = first_seen
    else:
        try:
            seen = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
        except ValueError:
            return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (_now(now) - seen).days


def is_engaged(application_status) -> bool:
    return (application_status or "") in ENGAGED_STATUSES


def lifecycle_state(first_seen, application_status=None, now=None) -> str:
    """One of ACTIVE / STALE / ARCHIVED."""
    if is_engaged(application_status):
        return ACTIVE
    age = age_days(first_seen, now=now)
    if age is None:
        return ACTIVE
    if age >= ARCHIVE_AFTER_DAYS:
        return ARCHIVED
    if age >= STALE_AFTER_DAYS:
        return STALE
    return ACTIVE
