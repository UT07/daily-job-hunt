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
