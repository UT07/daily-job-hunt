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


def test_counted_range_past_the_end_is_a_416_like_postgrest():
    """Measured against production 2026-10-09 on `jobs`, offset=100, limit=100:
    with `Prefer: count=exact` PostgREST answers 416 PGRST103; without a count
    it answers 200 []. Offset 0 on an empty set is 200 either way."""
    from postgrest.exceptions import APIError

    db = FakeSupabase({"jobs": [{"job_id": "a", "user_id": "u"}]})
    with pytest.raises(APIError) as exc:
        db.table("jobs").select("*", count="exact").range(100, 199).execute()
    assert exc.value.code == "PGRST103"
    assert db.table("jobs").select("*").range(100, 199).execute().data == []
    assert FakeSupabase().table("jobs").select("*", count="exact").range(0, 99).execute().data == []


def test_exact_count_is_the_total_not_the_page():
    db = FakeSupabase({"jobs": [{"job_id": str(i), "user_id": "u"} for i in range(30)]})
    res = db.table("jobs").select("*", count="exact").range(0, 9).execute()
    assert (len(res.data), res.count) == (10, 30)


def test_injected_failures_raise_for_that_statement_only():
    db = _two_users_one_job()
    db.fail_on[("jobs", "insert")] = RuntimeError("PGRST204")
    with pytest.raises(RuntimeError):
        db.table("jobs").insert({"job_id": "z", "user_id": "u"}).execute()
    assert len(db.table("jobs").select("*").execute().data) == 2  # reads unaffected


def test_unknown_methods_raise_rather_than_mock():
    db = _two_users_one_job()
    with pytest.raises(AttributeError):
        db.table("jobs").ilike("title", "%x%")


# ── upsert: PostgREST's two resolutions ─────────────────────────────────────
# supabase-py's upsert sends `Prefer: resolution=merge-duplicates` by default
# and `resolution=ignore-duplicates` with ignore_duplicates=True
# (postgrest/base_request_builder.py pre_upsert). Merge is INSERT ... ON
# CONFLICT DO UPDATE; ignore is ON CONFLICT DO NOTHING, and with
# return=representation PostgREST returns only the rows it actually inserted.

def _raw(**over):
    return {"job_hash": "h1", "location": "Dublin", "apply_url": "https://a", **over}


def test_upsert_merge_overwrites_the_conflicting_row():
    db = FakeSupabase({"jobs_raw": [_raw()]})
    res = db.table("jobs_raw").upsert(_raw(location="Cork"), on_conflict="job_hash").execute()
    assert db.rows("jobs_raw")[0]["location"] == "Cork"
    assert res.data and res.data[0]["location"] == "Cork"


def test_upsert_ignore_duplicates_leaves_the_existing_row_untouched():
    db = FakeSupabase({"jobs_raw": [_raw()]})
    res = (db.table("jobs_raw")
           .upsert(_raw(location="Cork", apply_url="https://evil"),
                   on_conflict="job_hash", ignore_duplicates=True).execute())
    assert db.rows("jobs_raw") == [_raw()]
    assert res.data == [], "DO NOTHING returns no representation for a skipped row"


def test_upsert_ignore_duplicates_still_inserts_a_new_row():
    db = FakeSupabase({"jobs_raw": []})
    res = (db.table("jobs_raw")
           .upsert(_raw(), on_conflict="job_hash", ignore_duplicates=True).execute())
    assert db.rows("jobs_raw") == [_raw()]
    assert res.data == [_raw()]


def test_or_matches_either_column_and_still_ands_with_eq():
    db = FakeSupabase({"jobs": [
        {"job_id": "1", "user_id": "u", "job_hash": "h1", "canonical_hash": None},
        {"job_id": "2", "user_id": "u", "job_hash": None, "canonical_hash": "h1"},
        {"job_id": "3", "user_id": "v", "job_hash": "h1", "canonical_hash": None},
        {"job_id": "4", "user_id": "u", "job_hash": "h2", "canonical_hash": "h2"},
    ]})
    got = (db.table("jobs").select("*").eq("user_id", "u")
           .or_("job_hash.eq.h1,canonical_hash.eq.h1").execute().data)
    assert sorted(r["job_id"] for r in got) == ["1", "2"]


def test_or_refuses_shapes_it_does_not_model():
    db = FakeSupabase({"jobs": []})
    with pytest.raises(NotImplementedError):
        db.table("jobs").select("*").or_("job_hash.ilike.h%,canonical_hash.eq.h1")


def test_upsert_rejects_keywords_supabase_py_does_not_have():
    db = FakeSupabase({"jobs_raw": []})
    with pytest.raises(TypeError):
        db.table("jobs_raw").upsert(_raw(), on_conflict="job_hash", ignoreDuplicates=True)
