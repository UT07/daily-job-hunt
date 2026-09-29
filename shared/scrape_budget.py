"""Deadline awareness for scrapers that enrich listings with per-job detail fetches.

Every card-based scraper here follows the same shape:

    for card in cards:
        full_desc = _fetch_job_detail(...)    # one slow proxied request per job
        ...
    db.table("jobs_raw").upsert(all_jobs)     # <- the write, AFTER the loop

The write happening last is what makes a timeout catastrophic rather than
merely lossy. Detail fetches go through the Bright Data proxy one at a time and
can hang to the socket timeout, so a single page of cards can outlast the whole
Lambda budget. When it does, the invocation is killed before the upsert and
EVERY job already collected is discarded — the Step Functions Catch routes to
<source>Failed and the run records count: 0.

Measured on Indeed, 2026-09-27/28/29: three consecutive daily runs found cards,
timed out during enrichment at 300s, and wrote nothing. Indeed had produced no
jobs for days while its logs showed it working.

A job carrying only its card snippet is worth incomparably more than no job at
all, so past the reserve the scraper stops enriching and keeps what it has.
"""

# Time to leave for the dedup pass and the jobs_raw upsert after the loop.
# Too small and the write itself gets killed, which is the failure being fixed.
DETAIL_RESERVE_MS = 45_000


def enrichment_budget_left(context, reserve_ms: int = DETAIL_RESERVE_MS) -> bool:
    """Whether there is still time to fetch another job detail.

    Anything other than a live Lambda context means no deadline: local runs and
    tests must not be throttled, and a context that misbehaves must never
    itself become the reason a scrape fails.
    """
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(getter):
        return True
    try:
        return float(getter()) > reserve_ms
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------
#
# A scraper skips its run when jobs_raw already holds rows newer than
# `now - cache_ttl_hours`. Set that TTL equal to the schedule interval and the
# check is a coin flip decided by microseconds:
#
#   run N   computes its floor at 07:01:06, scrapes, writes rows at 07:01:12
#   run N+1 computes its floor at 07:01:06 the next day — 6 SECONDS BEFORE
#           yesterday's write — sees them, and returns cached with zero requests
#
# The write timestamp is always later than that run's own floor by exactly the
# scrape duration, so at TTL == interval the next run is always locked out. A
# skipped day writes nothing, so the day after does scrape: the steady state is
# scrape / skip / scrape / skip.
#
# Measured 2026-09-29 on a cron(0 7 ? * MON-FRI) schedule: every scraper
# returned {"cached": true}, the state machine reported SUCCEEDED with
# count 1721, and jobs_raw gained 0 rows against 837 the day before. The cached
# branch returns the ROW COUNT as `count`, so the execution output is
# indistinguishable from a real scrape.
#
# The margin must exceed the longest plausible scrape duration. LinkedIn's was
# 127s; four hours is generous and still leaves the TTL well inside the next
# interval.
_SCHEDULE_MARGIN_HOURS = 4

#: Daily-schedule default. 20h, not 24h — see above.
DEFAULT_CACHE_TTL_HOURS = 24 - _SCHEDULE_MARGIN_HOURS


def cache_ttl_hours(nominal: int) -> int:
    """A cache TTL that cannot lock out the next scheduled run.

    `nominal` is the intended freshness window — 24h for a daily source, 48h
    for one that moves slowly. The margin is subtracted so the floor lands
    comfortably before the previous run's writes rather than microseconds
    after them.
    """
    return max(1, int(nominal) - _SCHEDULE_MARGIN_HOURS)
