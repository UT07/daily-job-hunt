"""main.scrape_all_jobs returns at its deadline even when a scraper hangs.

Both phases used `with ThreadPoolExecutor(...)` and `as_completed(futures)`
with no timeout, then `future.result(timeout=30)`. That timeout bounded
nothing: as_completed only yields futures that are already done, so result()
never waits, and the deadline check ran only when SOME future completed. A
hung scraper therefore blocked as_completed indefinitely, and even after a
break the `with` exit called shutdown(wait=True) and joined the hung thread.

Same defect and same fix as ai_client._unwaited_executor: bound the wait with
as_completed(timeout=...) and exit with shutdown(wait=False, cancel_futures=True).

The fake scraper sleeps on an Event (released at teardown) rather than
time.sleep, so an abandoned thread does not outlive the test by seconds.
"""
import threading
import time
from types import SimpleNamespace

import pytest

import main

HANG_S = 5.0
_release = threading.Event()


@pytest.fixture(autouse=True)
def _release_hung_threads():
    _release.clear()
    yield
    _release.set()


class _Scraper:
    def __init__(self, name, hang=False):
        self.name = name
        self.hang = hang

    def search(self, query, location, days_back=3):
        if self.hang:
            _release.wait(HANG_S)
            return [SimpleNamespace(title="late", company=self.name)]
        return [SimpleNamespace(title="on time", company=self.name)]


def _config(deadline_s):
    return {
        "search": {"queries": ["sre"], "days_back": 1,
                   "locations": {"primary": ["Dublin"], "secondary": []}},
        "scrapers": {"max_scrape_minutes": deadline_s / 60, "max_workers": 4},
    }


def _run(scrapers, deadline_s=0.5):
    t0 = time.monotonic()
    jobs = main.scrape_all_jobs(scrapers, _config(deadline_s))
    return jobs, time.monotonic() - t0


def test_a_hung_api_scraper_does_not_hold_the_phase_past_its_deadline():
    jobs, elapsed = _run([_Scraper("fast_api"), _Scraper("hung_api", hang=True)])
    assert elapsed < HANG_S / 2, f"returned after {elapsed:.1f}s; the deadline was 0.5s"
    assert [j.company for j in jobs] == ["fast_api"], "the on-time result must be kept"


def test_a_hung_browser_scraper_does_not_hold_the_phase_past_its_deadline():
    # "linkedin" is in BROWSER_SCRAPERS, so this exercises phase 2.
    assert "linkedin" in main.BROWSER_SCRAPERS
    jobs, elapsed = _run([_Scraper("linkedin", hang=True)], deadline_s=0.5)
    assert elapsed < HANG_S / 2, f"returned after {elapsed:.1f}s; the deadline was 0.5s"
    assert jobs == []


def test_without_a_hang_every_result_is_collected():
    # Control: the bound must not cut off scrapers that finish in time.
    jobs, elapsed = _run([_Scraper("a"), _Scraper("b"), _Scraper("linkedin")], deadline_s=5)
    # Browser scrapers run every consolidated BROWSER_QUERY per location.
    want = ["a", "b"] + ["linkedin"] * len(main.BROWSER_QUERIES)
    assert sorted(j.company for j in jobs) == want
    assert elapsed < 2
