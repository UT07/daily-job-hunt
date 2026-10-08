"""A jobs UPDATE must match on either hash column and report what it hit.

THE DEFECT (audit, 2026-10-08). save_job and tailor_resume updated `jobs` by
user_id + job_hash and reported saved: True without checking a row matched.
Rows created by Add Job (app._find_or_create_job) carry the hash in
canonical_hash with job_hash NULL, so for every manually added job the filter
hit ZERO rows, PostgREST answered success, and the compiled résumé was never
linked — the shape measured on b3026c307c74 and fixed in
scripts/reconcile_resume_rows.py the same day.

The table double below evaluates the filters it is given (eq, and the
`or=(a.eq.x,b.eq.y)` expression) against real rows, so "matched" means what
it means in PostgREST, rather than what a MagicMock returns by default.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "lambdas/pipeline")

import save_job  # noqa: E402

HASH = "b3026c307c74"
MANUAL = {"job_id": "j-manual", "user_id": "u-1", "job_hash": None, "canonical_hash": HASH}
SCRAPED = {"job_id": "j-scraped", "user_id": "u-1", "job_hash": HASH, "canonical_hash": None}


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.payload = None
        self.or_calls = []

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def or_(self, expr):
        self.or_calls.append(expr)
        terms = [t.split(".eq.") for t in expr.split(",")]
        self.filters.append(lambda r: any(r.get(c) == v for c, v in terms))
        return self

    def execute(self):
        hit = [r for r in self.rows if all(f(r) for f in self.filters)]
        for r in hit:
            r.update(self.payload)
        return MagicMock(data=[dict(r) for r in hit])


def _db(rows):
    tables = {}

    def table(name):
        tables[name] = _Table(rows)
        return tables[name]
    db = MagicMock()
    db.table.side_effect = table
    db.tables = tables
    return db


class TestUpdateJobRow:
    def test_the_double_reproduces_the_bug_with_the_old_filter(self):
        """Soundness (#6): the old job_hash-only filter matches nothing here."""
        t = _Table([dict(MANUAL)])
        assert t.update({"x": 1}).eq("user_id", "u-1").eq("job_hash", HASH).execute().data == []

    def test_a_manually_added_row_is_matched_by_canonical_hash(self):
        rows = [dict(MANUAL)]
        assert save_job.update_job_row(_db(rows), "u-1", HASH, {"resume_s3_key": "k"}) == 1
        assert rows[0]["resume_s3_key"] == "k"

    def test_a_scraped_row_is_still_matched_by_job_hash(self):
        rows = [dict(SCRAPED)]
        assert save_job.update_job_row(_db(rows), "u-1", HASH, {"resume_s3_key": "k"}) == 1

    def test_another_users_row_is_not_touched(self):
        rows = [dict(MANUAL, user_id="u-2")]
        assert save_job.update_job_row(_db(rows), "u-1", HASH, {"resume_s3_key": "k"}) == 0
        assert "resume_s3_key" not in rows[0]

    def test_a_non_hex_hash_is_never_interpolated_into_or(self):
        db = _db([dict(SCRAPED, job_hash="x),user_id.neq.(")])
        save_job.update_job_row(db, "u-1", "x),user_id.neq.(", {"a": 1})
        assert db.tables["jobs"].or_calls == []


def _save(rows):
    event = {"job_hash": HASH, "user_id": "u-1",
             "compile_result": {"pdf_s3_key": "users/u-1/resumes/x.pdf",
                                "page_violations": [], "ats_violations": []}}
    s3 = MagicMock()
    s3.client.return_value.generate_presigned_url.return_value = "https://signed"
    with patch("save_job.boto3", s3), patch("save_job.get_supabase", return_value=_db(rows)):
        return save_job.handler(event, None)


class TestSaveJob:
    def test_a_manually_added_job_is_linked_and_reported_saved(self):
        rows = [dict(MANUAL)]
        out = _save(rows)
        assert out["saved"] is True
        assert rows[0]["resume_s3_key"] == "users/u-1/resumes/x.pdf"

    def test_a_zero_row_update_is_not_reported_as_saved(self):
        out = _save([])
        assert out["saved"] is False, "a write that hit no row was reported as saved"


class TestTailorResume:
    def test_a_zero_row_update_is_reported(self):
        import tailor_resume
        from tests.unit.test_reported_fields_describe_the_shipped_document import _run
        with patch.object(tailor_resume, "update_job_row", side_effect=_recording(0)):
            _, out, _ = _run(tailor_resume_body())
        assert out["job_row_saved"] is False

    def test_a_matched_update_is_reported(self):
        import tailor_resume
        from tests.unit.test_reported_fields_describe_the_shipped_document import _run
        with patch.object(tailor_resume, "update_job_row", side_effect=_recording(1)):
            _, out, _ = _run(tailor_resume_body())
        assert out["job_row_saved"] is True

    def test_it_uses_the_shared_matcher(self):
        import tailor_resume
        assert tailor_resume.update_job_row is save_job.update_job_row


def _recording(matched):
    """Stand-in for update_job_row that still issues the update on the double
    (the shared _run helper reads the payload from it) and returns `matched`."""
    def fn(db, user_id, job_hash, update):
        db.table("jobs").update(update)
        return matched
    return fn


def tailor_resume_body():
    from tests.unit.realistic_resume_body import body
    return body(summary="Payments engineer.")
