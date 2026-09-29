"""Indeed found the jobs and then threw them away.

Production logs, 2026-09-29:

    [indeed] Query 'Site Reliability Engineer': N cards via mosaic_json
    [indeed] Detail fetch failed for 5f3fbe1e: The read operation timed out   x6
    REPORT ... Status: timeout

The listing worked. Enrichment did not: each card with a short snippet triggers
a per-job detail fetch through the Bright Data proxy, serially, and enough of
them hang that the 300s Lambda budget runs out.

The damage is not the missing descriptions. `db.table("jobs_raw").upsert(...)`
runs only AFTER the whole loop, so a timeout means NOTHING is written — every
card already collected is lost, the Step Functions Catch routes to IndeedFailed,
and the run records count: 0. Three consecutive daily runs did exactly this,
which is why Indeed has produced no jobs for days.

A partial description is worth far more than no job at all.
"""
import sys

sys.path.insert(0, "lambdas/scrapers")
sys.path.insert(0, ".")
import scrape_indeed  # noqa: E402
from shared.scrape_budget import (  # noqa: E402
    DETAIL_RESERVE_MS, enrichment_budget_left,
)


class _Ctx:
    def __init__(self, ms):
        self._ms = ms

    def get_remaining_time_in_millis(self):
        return self._ms


def test_plenty_of_time_means_keep_enriching():
    assert enrichment_budget_left(_Ctx(280_000)) is True


def test_running_out_of_time_stops_enrichment():
    """Must leave room for the dedup pass and the upsert — the write is the
    whole point, and it happens after the loop."""
    assert enrichment_budget_left(_Ctx(10_000)) is False


def test_the_reserve_is_large_enough_to_finish_the_write():
    assert DETAIL_RESERVE_MS >= 30_000, (
        "too small a reserve and the upsert itself gets killed, which is the "
        "exact failure being fixed"
    )


def test_no_lambda_context_means_no_deadline():
    """Local runs and tests have no context; they must not be throttled."""
    assert enrichment_budget_left(None) is True


def test_a_context_without_the_method_does_not_crash():
    class Odd:
        pass
    assert enrichment_budget_left(Odd()) is True


def test_a_context_that_raises_is_treated_as_unlimited():
    """Never let the guard itself become the thing that fails the scrape."""
    class Angry:
        def get_remaining_time_in_millis(self):
            raise RuntimeError("no")
    assert enrichment_budget_left(Angry()) is True


def test_the_card_loop_actually_consults_the_budget():
    """A guard defined but not called is the bug still present.

    Asserted on the source because the handler needs Supabase, httpx and a
    proxy to run, and mocking all three would test the mocks.
    """
    import inspect
    src = inspect.getsource(scrape_indeed.handler)
    assert "enrichment_budget_left" in src, (
        "the deadline guard is not consulted inside handler()"
    )
    # and it must be checked before the detail fetch, not after
    guard = src.index("enrichment_budget_left")
    fetch = src.index("_fetch_job_detail")
    assert guard < fetch, "the budget is checked after the fetch it is meant to prevent"


# ---------------------------------------------------------------------------
# The same shape exists in linkedin and glassdoor
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize("module", ["scrape_indeed", "scrape_linkedin", "scrape_glassdoor"])
def test_every_card_scraper_guards_its_detail_fetch(module):
    """indeed, linkedin and glassdoor all fetch details in a loop and upsert
    afterwards. Fixing only the one that happened to fail leaves the other two
    one slow proxy day away from the same total loss."""
    import importlib
    import inspect
    m = importlib.import_module(module)
    src = inspect.getsource(m.handler)
    assert "enrichment_budget_left" in src, f"{module}.handler has no deadline guard"
    assert src.index("enrichment_budget_left") < src.index("_fetch_job_detail"), (
        f"{module} checks the budget after the fetch it is meant to prevent"
    )


def test_the_guard_lives_in_one_place():
    """Copy-pasting it into three files is how the % escaping bug survived at
    two call sites for months."""
    import pathlib as _p
    for name in ("scrape_indeed", "scrape_linkedin", "scrape_glassdoor"):
        src = _p.Path(f"lambdas/scrapers/{name}.py").read_text()
        # Matched loosely: the same import line also carries cache_ttl_hours,
        # and an exact-string assertion here breaks whenever a second helper
        # is added to the module — which it did.
        import re as _re
        assert _re.search(
            r"from shared\.scrape_budget import [^\n]*enrichment_budget_left", src
        ), f"{name} does not import the shared guard"
        assert "def enrichment_budget_left" not in src, f"{name} redefines the helper"
