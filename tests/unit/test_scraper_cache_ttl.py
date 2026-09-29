"""A cache TTL equal to the schedule interval turns every other run into a no-op.

Measured 2026-09-29 on cron(0 7 ? * MON-FRI): every scraper returned
{"cached": true}, issued zero HTTP requests, and the state machine reported
SUCCEEDED with count 1721. jobs_raw gained 0 rows, against 837 the day before.

The arithmetic is inescapable at TTL == interval. A run computes its cache
floor at handler start and writes rows when the scrape finishes, so the write
timestamp is always LATER than that run's own floor, by the scrape duration.
Exactly one interval later the next run's floor lands before those rows, sees
them, and skips. Margins from the real 09-29 run:

    ashby       floor 07:01:06.735   newest row 07:01:12.067   +5.33s inside
    greenhouse  floor 07:01:07.622   newest row 07:01:22.377  +14.75s inside
    linkedin    floor 07:01:06.392   newest row 07:03:13.221 +126.83s inside

A skipped day writes nothing, so the following day does scrape — the steady
state is scrape / skip / scrape / skip, at roughly half the intended coverage.

It hid because the cached branch returns the ROW COUNT as `count`, making the
output identical in shape to a real scrape.
"""
import pathlib
import re

import pytest

sys_scrapers = pathlib.Path("lambdas/scrapers")

# nominal freshness window each source intends
NOMINAL = {
    "scrape_ashby.py": 24, "scrape_greenhouse.py": 24, "scrape_linkedin.py": 24,
    "scrape_glassdoor.py": 24, "scrape_adzuna.py": 24, "scrape_indeed.py": 24,
    "scrape_irish.py": 24, "scrape_apify.py": 24, "scrape_yc.py": 48,
    "scrape_hn.py": 168,
}

# The pipeline runs daily; anything at or above this locks out the next run.
SCHEDULE_INTERVAL_HOURS = 24


def _ttl_expr(name: str) -> str:
    src = (sys_scrapers / name).read_text()
    m = re.search(r'cache_ttl_hours = event\.get\("cache_ttl_hours", ([^)]+)\)', src)
    assert m, f"{name}: no cache TTL default found"
    return m.group(1).strip()


@pytest.mark.parametrize("name", sorted(NOMINAL))
def test_no_scraper_hardcodes_a_bare_number(name):
    """A literal here is how ten files drifted to the same broken value."""
    expr = _ttl_expr(name)
    assert not expr.isdigit(), (
        f"{name} hardcodes {expr}h. Use cache_ttl_hours() so the schedule margin "
        "is applied in one place."
    )


@pytest.mark.parametrize("name,nominal", sorted(NOMINAL.items()))
def test_the_effective_ttl_leaves_room_before_the_next_run(name, nominal):
    import sys
    sys.path.insert(0, ".")
    from shared.scrape_budget import cache_ttl_hours
    effective = cache_ttl_hours(nominal)
    assert effective < nominal, f"{name}: no margin applied"
    # For a daily source the floor must land inside the previous interval.
    if nominal <= SCHEDULE_INTERVAL_HOURS:
        assert effective < SCHEDULE_INTERVAL_HOURS, (
            f"{name}: {effective}h TTL on a {SCHEDULE_INTERVAL_HOURS}h schedule "
            "still locks out the next run"
        )


def test_the_margin_exceeds_the_longest_observed_scrape():
    """LinkedIn's write landed 126.8s after its own floor. The margin has to
    cover the slowest scrape, or the race comes back."""
    import sys
    sys.path.insert(0, ".")
    from shared.scrape_budget import _SCHEDULE_MARGIN_HOURS
    assert _SCHEDULE_MARGIN_HOURS * 3600 > 127, "margin narrower than a real scrape"


def test_the_default_is_under_a_day():
    import sys
    sys.path.insert(0, ".")
    from shared.scrape_budget import DEFAULT_CACHE_TTL_HOURS
    assert DEFAULT_CACHE_TTL_HOURS < SCHEDULE_INTERVAL_HOURS
