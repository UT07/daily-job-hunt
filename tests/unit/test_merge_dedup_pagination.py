"""merge_dedup must read every row, not PostgREST's first 1000.

THE DEFECT (audit, 2026-10-08). merge_dedup's reads of `jobs_raw` (today, and
the 30-day backfill) and of the user's existing `jobs` were unpaginated.
PostgREST caps a response at ~1000 rows and says nothing. The `jobs` table
holds ~1,400 rows for the one live user, so the "already scored" set silently
missed ~400 hashes and company+title keys, and those jobs came back as NEW —
re-scored and re-tailored on every run. Same class as the dashboard total that
read 1000 for 1197 jobs (17b3b48).

The double below serves at most 1000 rows per request, as PostgREST does, and
ignores everything past the requested range — so an unpaginated read sees only
the first page here exactly as in production.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "lambdas/pipeline")

import merge_dedup  # noqa: E402

PAGE = 1000


def _pages(total):
    rows = [{"job_hash": f"h{i:05d}"} for i in range(total)]
    calls = []

    def build(lo, hi):
        calls.append((lo, hi))
        q = MagicMock()
        q.execute.return_value = MagicMock(data=rows[lo:hi + 1][:PAGE])
        return q
    return build, calls


class TestAllRows:
    def test_a_short_page_ends_the_walk(self):
        build, calls = _pages(42)
        assert len(merge_dedup._all_rows(build)) == 42 and len(calls) == 1

    def test_exactly_one_full_page_is_not_mistaken_for_the_end(self):
        """1000 looks like a complete answer and is the one number never to trust."""
        build, calls = _pages(PAGE)
        assert len(merge_dedup._all_rows(build)) == PAGE
        assert len(calls) == 2

    def test_the_real_table_size(self):
        build, calls = _pages(1400)
        assert len(merge_dedup._all_rows(build)) == 1400
        assert calls == [(0, 999), (1000, 1999)]

    def test_no_rows(self):
        build, calls = _pages(0)
        assert merge_dedup._all_rows(build) == [] and len(calls) == 1


class _Paged:
    """A PostgREST-shaped chain: filters are accepted, `.range` is honoured,
    and an unranged execute returns only the first 1000 rows."""

    def __init__(self, rows, backfill_rows=()):
        self.rows, self.lo, self.hi = rows, 0, PAGE - 1
        self.backfill_rows = list(backfill_rows)

    def __getattr__(self, name):          # select / eq / gte / order / is_
        return lambda *a, **k: self

    def lt(self, *a, **k):
        # Only the 30-day backfill query has an upper bound; it gets its own
        # rows so the "today" read and the backfill read are distinguishable.
        # (The first version served the same rows to both, so reverting the
        # today read to unpaginated SURVIVED: the backfill re-supplied them.)
        self.rows = self.backfill_rows
        return self

    @property
    def not_(self):
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def execute(self):
        return MagicMock(data=self.rows[self.lo:self.hi + 1][:PAGE])


def _db(raw_rows, existing_rows, backfill_rows=()):
    db = MagicMock()
    empty = MagicMock()
    for attr in ("select", "eq", "gte", "lt", "order", "range", "limit"):
        getattr(empty, attr).return_value = empty
    empty.execute.return_value = MagicMock(data=[])
    db.table.side_effect = lambda name: (
        _Paged(raw_rows, backfill_rows) if name == "jobs_raw"
        else _Paged(existing_rows) if name == "jobs" else empty)
    return db


def test_a_job_scored_beyond_row_1000_is_not_new_again():
    existing = [{"job_hash": f"old{i:05d}", "company": f"Co{i}", "title": f"Role {i}"}
                for i in range(1400)]
    late = existing[1200]
    job = {"job_hash": late["job_hash"], "title": "Senior Python Engineer",
           "company": "Acme", "source": "linkedin", "location": "Dublin",
           "description": "Python and AWS. " * 40, "posted_date": None}
    with patch("merge_dedup.get_supabase", return_value=_db([job], existing)):
        out = merge_dedup.handler({"user_id": "user-1"}, None)
    assert late["job_hash"] not in out["new_job_hashes"], (
        "a job already in `jobs` at row 1201 was treated as new: the existing "
        "set was read unpaginated and stopped at 1000")


def test_the_double_admits_the_same_job_when_it_is_not_already_scored():
    """Soundness (#6): without this, the test above could pass because the
    pre-filter rejected the job for an unrelated reason."""
    job = {"job_hash": "fresh-1", "title": "Senior Python Engineer",
           "company": "Acme", "source": "linkedin", "location": "Dublin",
           "description": "Python and AWS. " * 40, "posted_date": None}
    with patch("merge_dedup.get_supabase", return_value=_db([job], [])):
        out = merge_dedup.handler({"user_id": "user-1"}, None)
    assert "fresh-1" in out["new_job_hashes"]


def test_raw_rows_beyond_1000_reach_dedup(caplog):
    """Exactly 1001 raw rows: the handler's own summary line must count all of
    them. (Fuzzy dedup then collapses these identical titles, so the count of
    NEW jobs is not the measure; the count read is.)"""
    import logging
    raw = [{"job_hash": f"r{i:05d}", "title": "Senior Python Engineer",
            "company": f"Co{i}", "source": "linkedin", "location": "Dublin",
            "description": "Python and AWS. " * 40, "posted_date": None}
           for i in range(1001)]
    with caplog.at_level(logging.INFO), \
         patch("merge_dedup.get_supabase", return_value=_db(raw, [])):
        merge_dedup.handler({"user_id": "user-1"}, None)
    assert "1001 scraped" in caplog.text, (
        "the raw read stopped at PostgREST's 1000-row page")


def test_backfill_rows_beyond_1000_reach_dedup(caplog):
    import logging
    raw = [{"job_hash": f"b{i:05d}", "title": "Senior Python Engineer",
            "company": f"Co{i}", "source": "linkedin", "location": "Dublin",
            "description": "Python and AWS. " * 40, "posted_date": None}
           for i in range(1001)]
    with caplog.at_level(logging.INFO), \
         patch("merge_dedup.get_supabase", return_value=_db([], [], backfill_rows=raw)):
        merge_dedup.handler({"user_id": "user-1"}, None)
    assert "Backfill: 1001 unscored jobs" in caplog.text, (
        "the 30-day backfill read stopped at PostgREST's 1000-row page")
