"""Save & Score's apply_url must reach the row.

score_job passed `apply_url` to `_find_or_create_job`, and neither the INSERT
row nor the merge UPDATE wrote it -- merge_manual_job even computed the merged
value, which was then discarded. Auto-apply reads apply_url, so a manually
added job had no link to apply through.

An empty apply_url must never overwrite a stored one: the form sends "" when
the user leaves the field blank.
"""
import pytest

import app as app_module
from tests.unit.postgrest_double import FakeSupabase
from utils.canonical_hash import canonical_hash

JD = "Operate Kubernetes and write Python for a payments platform team. " * 3
CHASH = canonical_hash("Acme", "SRE", JD)


def _payload(url):
    return {"company": "Acme", "title": "SRE", "description": JD, "apply_url": url}


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({"jobs": []})
    monkeypatch.setattr(app_module, "_db", fake)
    return fake


def _existing(db, url):
    db.tables["jobs"].append({"job_id": CHASH, "user_id": "alice", "canonical_hash": CHASH,
                              "title": "SRE", "company": "Acme", "description": JD,
                              "source": "linkedin", "apply_url": url})


def test_a_new_job_keeps_its_apply_url(db):
    assert app_module._find_or_create_job("alice", _payload("https://acme.example/apply"))
    assert db.rows("jobs", user_id="alice")[0]["apply_url"] == "https://acme.example/apply"


def test_a_merge_fills_a_missing_apply_url(db):
    _existing(db, None)
    app_module._find_or_create_job("alice", _payload("https://acme.example/apply"))
    assert db.rows("jobs", user_id="alice")[0]["apply_url"] == "https://acme.example/apply"


def test_a_merge_with_a_new_url_takes_the_manual_one(db):
    _existing(db, "https://old.example")
    app_module._find_or_create_job("alice", _payload("https://new.example"))
    assert db.rows("jobs", user_id="alice")[0]["apply_url"] == "https://new.example"


def test_an_empty_url_never_erases_a_stored_one(db):
    _existing(db, "https://old.example")
    app_module._find_or_create_job("alice", _payload(""))
    assert db.rows("jobs", user_id="alice")[0]["apply_url"] == "https://old.example"


def test_save_and_score_end_to_end(db, monkeypatch, inline_tasks):
    """POST /api/score -> 202 -> the "score" task -> the row. apply_url has to
    survive the task payload's trip through pipeline_tasks, which is the hop
    the 72.6s/503 fix added between the request and the write."""
    from fastapi.testclient import TestClient

    from auth import AuthUser, get_current_user
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "x"})
    monkeypatch.setattr(app_module, "_posthog", None)
    monkeypatch.setattr(app_module, "score_single_job_deterministic", lambda *a, **k: {
        "match_score": 80, "ats_score": 80, "hiring_manager_score": 80,
        "tech_recruiter_score": 80, "reasoning": "ok"})
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id="alice", email="a@x")
    try:
        r = TestClient(app_module.app).post("/api/score", json={
            "job_description": JD, "job_title": "SRE", "company": "Acme",
            "apply_url": "https://acme.example/apply", "force": True})
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 202, r.text
    out = inline_tasks[r.json()["task_id"]]["result"]
    assert out["saved"] is True
    assert db.rows("jobs", user_id="alice")[0]["apply_url"] == "https://acme.example/apply"
