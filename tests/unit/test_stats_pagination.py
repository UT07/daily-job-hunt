"""The dashboard's totals must count rows, not pages.

`get_stats` did `total = len(response.data)` on an unpaginated select.
PostgREST answers at most ~1000 rows and says nothing about truncating, so for
a user with 1197 live jobs the dashboard displayed:

    TOTAL JOBS   1000        (actual: 1197)
    AVG SCORE      67        (the mean of an arbitrary 1000 of them)

A page size reported as a total is the same class of defect as a status that
cannot fail: the number looks plausible, is stable across reloads, and
describes something nobody asked about. It also gets *more* wrong as the
product succeeds, and it is exactly the shape that stops looking wrong once
you are used to it.

Verified against production 2026-10-08: Content-Range said 1197; the
unpaginated select returned 1000.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_client import SupabaseClient  # noqa: E402

PAGE = 1000


def _pages(total):
    """A query double that serves `total` rows 1000 at a time, like PostgREST."""
    rows = [{"match_score": 80, "application_status": "New"} for _ in range(total)]
    calls = []

    def build(lo, hi):
        calls.append((lo, hi))
        chunk = rows[lo:hi + 1]
        q = MagicMock()
        q.execute.return_value = MagicMock(data=chunk)
        return q

    return build, calls


class TestAllRows:
    def test_a_single_short_page_ends_the_walk(self):
        build, calls = _pages(42)
        assert len(SupabaseClient._all_rows(build)) == 42
        assert len(calls) == 1, "a short page is the end; it must not ask again"

    def test_exactly_one_full_page_is_not_mistaken_for_the_end(self):
        """The case that produced the bug. 1000 rows looks like a complete
        answer and is the one number that is never trustworthy."""
        build, calls = _pages(PAGE)
        assert len(SupabaseClient._all_rows(build)) == PAGE
        assert len(calls) == 2, (
            "a full page must be followed by another request; stopping there is "
            "exactly how 1197 jobs were reported as 1000")

    def test_more_than_one_page_is_counted_in_full(self):
        build, _ = SupabaseClient._all_rows, None
        build, calls = _pages(1197)
        assert len(SupabaseClient._all_rows(build)) == 1197, (
            "the real production count, and the number the dashboard showed as 1000")
        assert len(calls) == 2

    def test_several_pages(self):
        build, calls = _pages(2381)
        assert len(SupabaseClient._all_rows(build)) == 2381
        assert len(calls) == 3

    def test_no_rows_at_all(self):
        build, calls = _pages(0)
        assert SupabaseClient._all_rows(build) == []
        assert len(calls) == 1

    def test_the_ranges_requested_are_contiguous_and_non_overlapping(self):
        """An off-by-one here double-counts or drops a row per page, which is
        the kind of error a total hides perfectly."""
        build, calls = _pages(2500)
        SupabaseClient._all_rows(build)
        assert calls == [(0, 999), (1000, 1999), (2000, 2999)]


def test_get_stats_walks_rather_than_truncating():
    """The integration of it: 1197 rows in, 1197 reported out."""
    live = [{"match_score": 80, "application_status": "New"} for _ in range(1197)]

    class _Chain:
        def __init__(self, data):
            self._data, self._lo, self._hi = data, 0, len(data)
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def range(self, lo, hi):
            self._lo, self._hi = lo, hi
            return self
        def execute(self):
            return MagicMock(data=self._data[self._lo:self._hi + 1][:PAGE])

    client = MagicMock()
    client.table.side_effect = lambda name: _Chain(live if name == "jobs" else [])
    db = SupabaseClient.__new__(SupabaseClient)
    db.client = client

    stats = db.get_job_stats("u-1")
    assert stats["total_jobs"] == 1197, (
        f"reported {stats['total_jobs']} for 1197 live jobs — the page size, "
        "not the count")
