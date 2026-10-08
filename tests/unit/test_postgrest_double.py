"""The PostgREST double must fail the way PostgREST fails, or tests built on it
prove nothing (CLAUDE.md #6).

The decisive property is that filters are APPLIED. A double that ignored
`.eq("user_id", ...)` would pass every user-scoping test whether the scope was
there or not; these tests are the evidence that this one does not.
"""
import pytest

from tests.unit.postgrest_double import DuplicateKey, FakeSupabase, MissingWhere


def _two_users_one_job():
    return FakeSupabase({"jobs": [
        {"job_id": "h1", "user_id": "alice", "resume_s3_url": "a.pdf"},
        {"job_id": "h1", "user_id": "bob", "resume_s3_url": "b.pdf"},
    ]})


def test_an_update_keyed_only_by_job_id_hits_both_users():
    """The known-bad control: the unscoped write the audit found DOES clobber
    the other user's row here. If this ever passes vacuously, every scoping
    test below is meaningless."""
    db = _two_users_one_job()
    res = db.table("jobs").update({"resume_s3_url": "x"}).eq("job_id", "h1").execute()
    assert len(res.data) == 2
    assert {r["resume_s3_url"] for r in db.tables["jobs"]} == {"x"}


def test_a_scoped_update_touches_only_its_own_row():
    db = _two_users_one_job()
    res = (db.table("jobs").update({"resume_s3_url": "x"})
           .eq("job_id", "h1").eq("user_id", "alice").execute())
    assert [r["user_id"] for r in res.data] == ["alice"]
    assert db.rows("jobs", user_id="bob")[0]["resume_s3_url"] == "b.pdf"


def test_an_update_matching_nothing_returns_no_rows():
    db = _two_users_one_job()
    res = db.table("jobs").update({"x": 1}).eq("job_id", "nope").execute()
    assert res.data == []


def test_unfiltered_update_and_delete_are_refused():
    db = _two_users_one_job()
    with pytest.raises(MissingWhere):
        db.table("jobs").update({"x": 1}).execute()
    with pytest.raises(MissingWhere):
        db.table("jobs").delete().execute()


def test_insert_enforces_the_composite_primary_key():
    db = _two_users_one_job()
    with pytest.raises(DuplicateKey):
        db.table("jobs").insert({"job_id": "h1", "user_id": "alice"}).execute()
    # Same job_id, different user: allowed, exactly as (job_id, user_id) allows.
    db.table("jobs").insert({"job_id": "h1", "user_id": "carol"}).execute()
    assert len(db.rows("jobs", job_id="h1")) == 3


def test_select_filters_and_maybe_single():
    db = _two_users_one_job()
    got = db.table("jobs").select("*").eq("user_id", "bob").maybe_single().execute()
    assert got.data["resume_s3_url"] == "b.pdf"
    none = db.table("jobs").select("*").eq("user_id", "zed").maybe_single().execute()
    assert none.data is None


def test_is_and_not_is_null():
    db = FakeSupabase({"jobs": [
        {"job_id": "a", "user_id": "u", "k": None},
        {"job_id": "b", "user_id": "u", "k": ["x"]},
    ]})
    assert [r["job_id"] for r in db.table("jobs").select("*").is_("k", "null").execute().data] == ["a"]
    assert [r["job_id"] for r in db.table("jobs").select("*").not_.is_("k", "null").execute().data] == ["b"]


def test_pages_cap_at_1000_and_range_is_inclusive():
    db = FakeSupabase({"jobs": [{"job_id": str(i), "user_id": "u"} for i in range(1500)]})
    assert len(db.table("jobs").select("*").execute().data) == 1000
    assert len(db.table("jobs").select("*").range(1000, 1999).execute().data) == 500
    assert len(db.table("jobs").select("*").range(0, 0).execute().data) == 1


def test_unknown_methods_raise_rather_than_mock():
    db = _two_users_one_job()
    with pytest.raises(AttributeError):
        db.table("jobs").ilike("title", "%x%")
