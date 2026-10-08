"""Aggregates must read every row, not PostgREST's first page of 1000.

Two reads fed totals from an unpaginated select:

* get_job_stats' `jobs_with_status`, which folds each job's CURRENT status
  into the funnel (total_applied / interviewing / offers / rejected);
* /api/dashboard/skills, which counts key_matches across jobs.

Both use `_all_rows` now (commit 17b3b48 added it for the jobs read beside
them). The double caps pages at 1000 like PostgREST, so row 1001 is invisible
to an unpaginated read here exactly as in production.

Deliberately NOT done: an is_expired filter on `jobs_with_status`. Every
Applied/Withdrawn/Rejected row in production carries is_expired=True (the
posting 404'd after the user applied; see db_client.get_jobs' hide_expired),
so that filter would erase the user's applications from the funnel it feeds.
"""
import pytest

import app as app_module
from auth import AuthUser
from db_client import SupabaseClient
from tests.unit.postgrest_double import FakeSupabase


def _stats(rows):
    fake = FakeSupabase({"jobs": rows, "application_timeline": []})
    db = SupabaseClient.__new__(SupabaseClient)
    db.client = fake
    return db.get_job_stats("u")


def _applied(n, expired=True):
    return [{"job_id": f"j{i}", "user_id": "u", "application_status": "Applied",
             "match_score": 80, "is_expired": expired} for i in range(n)]


@pytest.mark.parametrize("n", [999, 1000, 1001, 2500])
def test_the_funnel_counts_every_applied_job(n):
    assert _stats(_applied(n))["total_applied"] == n


def test_expired_applications_still_count_as_applied():
    rows = _applied(3, expired=True) + [
        {"job_id": "x", "user_id": "u", "application_status": "New",
         "match_score": 70, "is_expired": False}]
    assert _stats(rows)["total_applied"] == 3


def test_another_users_jobs_do_not_count():
    rows = _applied(5) + [dict(r, user_id="other", job_id="o" + r["job_id"]) for r in _applied(7)]
    assert _stats(rows)["total_applied"] == 5


def _skills(rows, monkeypatch):
    monkeypatch.setattr(app_module, "_db", FakeSupabase({"jobs": rows}))
    return {s["name"]: s["count"] for s in
            app_module.get_dashboard_skills(AuthUser(id="u", email="u@x"))["skills"]}


def _job(i, skills, **kw):
    return {"job_id": f"j{i}", "user_id": "u", "is_expired": False, "key_matches": skills, **kw}


def test_skills_beyond_the_first_page_are_counted(monkeypatch):
    rows = [_job(i, ["Python"]) for i in range(1000)] + [_job(1000 + i, ["Rust"]) for i in range(3)]
    got = _skills(rows, monkeypatch)
    assert got == {"Python": 1000, "Rust": 3}


def test_exactly_one_full_page_is_neither_dropped_nor_double_counted(monkeypatch):
    rows = [_job(i, ["Python"]) for i in range(997)] + [_job(997 + i, ["Rust"]) for i in range(3)]
    assert _skills(rows, monkeypatch) == {"Python": 997, "Rust": 3}


def test_skills_keep_their_filters(monkeypatch):
    rows = ([_job(i, ["Go"]) for i in range(3)]
            + [_job(10 + i, ["Expired"], is_expired=True) for i in range(5)]
            + [_job(20 + i, ["Theirs"], user_id="other") for i in range(5)]
            + [_job(30 + i, None) for i in range(5)])
    assert _skills(rows, monkeypatch) == {"Go": 3}
