"""`/api/score`'s `saved` must mean a row was written, not that a hash exists.

`_find_or_create_job` swallowed an INSERT failure and still returned the
canonical hash. The follow-up UPDATE then matched zero rows -- which PostgREST
answers with 200 and an empty list, not an error -- and `saved = bool(job_id)`
reported True for a job that does not exist. CLAUDE.md #2: ask what the status
reports on a no-op run. It reported success.

Run against `postgrest_double.FakeSupabase`, whose UPDATE returns the rows it
actually touched (supabase-py's default returning=representation), so an
UPDATE that matched nothing is visible here exactly as it is in production.
"""
import pytest

import app as app_module
from auth import AuthUser
from tests.unit.postgrest_double import FakeSupabase

JD = "Build and operate Kubernetes platforms; Python, Terraform, on-call. " * 3
USER = AuthUser(id="alice", email="a@x")


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({"jobs": []})
    monkeypatch.setattr(app_module, "_db", fake)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "\\documentclass{x}"})
    monkeypatch.setattr(app_module, "score_single_job_deterministic", lambda *a, **k: {
        "match_score": 88, "ats_score": 88, "hiring_manager_score": 88,
        "tech_recruiter_score": 88, "reasoning": "fit",
    })
    return fake


def _score():
    # The fresh path's body, called directly: since the 72.6s/503 fix it runs
    # as a "score" task rather than inside POST /api/score, and returns
    # ScoreResponse(...).model_dump().
    req = app_module.ScoreRequest(job_description=JD, job_title="SRE", company="Acme", force=True)
    return app_module.ScoreResponse(**app_module._score_fresh(USER.id, req))


def test_a_fresh_job_is_saved_and_says_so(db):
    out = _score()
    assert out.saved is True
    (row,) = db.rows("jobs", user_id="alice")
    assert row["job_id"] == out.job_id and row["match_score"] == 88


def test_a_failed_insert_is_not_reported_as_saved(db):
    db.fail_on[("jobs", "insert")] = RuntimeError("PGRST204 column not in schema cache")
    out = _score()
    assert db.rows("jobs") == []
    assert out.saved is False, "nothing was written, so saved must be False"


def test_find_or_create_returns_nothing_when_the_insert_failed(db):
    db.fail_on[("jobs", "insert")] = RuntimeError("boom")
    assert app_module._find_or_create_job("alice", {
        "company": "Acme", "title": "SRE", "description": JD}) == ""


def test_an_update_that_matched_no_row_is_not_saved(db, monkeypatch):
    """The row vanished between create and score (deleted in another tab, or
    an id that was never real). The UPDATE succeeds and touches nothing."""
    monkeypatch.setattr(app_module, "_find_or_create_job", lambda uid, p: "ghost-id")
    out = _score()
    assert out.saved is False


def test_a_concurrent_create_still_counts_as_saved(db, monkeypatch):
    """The INSERT loses a race to an identical one: the PK collides, but the
    row the user wanted exists. That is a save, and the score must land on it."""
    from utils.canonical_hash import canonical_hash
    chash = canonical_hash("Acme", "SRE", JD)
    # The lookup by canonical_hash misses (it ran before the other insert
    # committed), so _find_or_create_job goes on to INSERT and collides.
    real_table = db.table
    state = {"lookups": 0}

    def table(name):
        q = real_table(name)
        if name == "jobs" and state["lookups"] == 0:
            state["lookups"] += 1
            db.tables["jobs"].append({"job_id": chash, "user_id": "alice",
                                      "canonical_hash": chash, "description": JD})
            q._filters.append(lambda r: False)  # this first read sees nothing
        return q

    monkeypatch.setattr(db, "table", table)
    out = _score()
    assert out.saved is True
    assert db.rows("jobs", user_id="alice")[0]["match_score"] == 88
