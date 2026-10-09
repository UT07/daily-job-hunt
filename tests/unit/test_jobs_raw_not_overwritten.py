"""A manual submission never rewrites a `jobs_raw` row someone else depends on.

`jobs_raw` is SHARED: it has no user_id, and the key is
canonical_hash(company, title, description). Two users who paste the same JD
get the same hash, and so does a pipeline-scraped posting with the same text.

`_upsert_jobs_raw` used `.upsert(..., on_conflict="job_hash")`, which is
PostgREST's merge-duplicates -- ON CONFLICT DO UPDATE. So user B's Save &
Score or Add Job rewrote the row user A's tailoring reads: location (which the
geo / work-auth cap scores against), apply_url and source are not part of the
hash, so B controlled them for A. Flagged by a security review as cross-tenant
data tampering; run-single on main already did this before the helper existed.

The write is now insert-if-absent (`ignore_duplicates=True`, ON CONFLICT DO
NOTHING). Nothing is lost by not overwriting: an identical hash means the same
company, title and description up to case and whitespace.

Over the PostgREST double, whose ignore_duplicates semantics are pinned in
test_postgrest_double.py first.
"""
import copy
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase
from utils.canonical_hash import canonical_hash

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs and incident response. " * 3)
A = {"job_description": JD, "job_title": "Site Reliability Engineer", "company": "Acme",
     "location": "Dublin", "apply_url": "https://acme.example/jobs/1",
     "resume_type": "sre_devops"}
B = {**A, "location": "Lagos", "apply_url": "https://attacker.example/phish"}
HASH = canonical_hash("Acme", "Site Reliability Engineer", JD)


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret",
                                 "SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:x:1:stateMachine:s"}):
        yield


class _Sfn:
    def start_execution(self, **_):
        return {"executionArn": "arn:aws:states:x:1:execution:s:e-1",
                "startDate": __import__("datetime").datetime(2026, 10, 9)}


@pytest.fixture
def world(monkeypatch, inline_tasks):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = FakeSupabase({"jobs": [], "jobs_raw": [], "user_resumes": [], "users": []})
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "bundled.tex"})
    monkeypatch.setattr(app_module, "_posthog", None)
    monkeypatch.setattr(app_module, "score_single_job_deterministic", lambda *a, **k: {
        "match_score": 82, "ats_score": 80, "hiring_manager_score": 84,
        "tech_recruiter_score": 82, "reasoning": "fits"})
    monkeypatch.setattr(app_module, "_get_sfn", lambda: _Sfn())
    who = {"id": "user-a"}
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=who["id"], email=f"{who['id']}@example.test")
    yield TestClient(app_module.app, raise_server_exceptions=False), db, who
    app_module.app.dependency_overrides.clear()


def test_a_first_submission_still_creates_the_row(world):
    client, db, _ = world
    assert client.post("/api/score", json=A).status_code == 202
    rows = db.rows("jobs_raw", job_hash=HASH)
    assert len(rows) == 1 and rows[0]["description"] == JD


@pytest.mark.parametrize("route", ["/api/score", "/api/pipeline/run-single"])
def test_user_b_cannot_rewrite_user_a_s_row(world, route):
    client, db, who = world
    assert client.post(route, json=A).status_code == 202
    before = copy.deepcopy(db.rows("jobs_raw", job_hash=HASH))
    assert before

    who["id"] = "user-b"
    assert client.post(route, json=B).status_code == 202
    assert db.rows("jobs_raw", job_hash=HASH) == before, (
        f"user B's {route} rewrote the shared jobs_raw row user A tailors from")


@pytest.mark.parametrize("route", ["/api/score", "/api/pipeline/run-single"])
def test_a_scraped_row_is_never_modified_by_a_manual_submission(world, route):
    client, db, _ = world
    scraped = {"job_hash": HASH, "title": "Site Reliability Engineer", "company": "Acme",
               "description": JD, "location": "Dublin, Ireland",
               "apply_url": "https://linkedin.example/view/1", "source": "linkedin",
               "posted_date": "2026-10-01"}
    db.tables["jobs_raw"].append(copy.deepcopy(scraped))
    assert client.post(route, json=B).status_code == 202
    assert db.rows("jobs_raw", job_hash=HASH) == [scraped]
