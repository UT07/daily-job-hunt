"""GET /api/gdpr/export must hand the user their data, all of it.

Live run, 2026-10-09 14:01 UTC, test user ee449fe1 with ONE job:

    GET /api/gdpr/export 500
    postgrest.exceptions.APIError: {'message': 'Requested range not
    satisfiable', 'code': 'PGRST103', 'hint': None, 'details': 'An offset of
    100 was requested, but there are only 1 rows.'}

`export_user_data` walked jobs with `db.get_jobs(page=n)` and stopped on an
empty page. Three defects in that one loop:

1. `get_jobs` returns `(rows, total)`. A tuple is never falsy, so the loop
   never saw an "empty page" and always asked for the next one.
2. `get_jobs` selects with `count="exact"`, and PostgREST answers a counted
   range past the end with 416 PGRST103 rather than `[]` (verified against
   production the same day). So every export of a user with under 100 jobs
   was a 500 — that is, every export.
3. Had it not crashed, it would have exported `[rows, total]` pairs, and
   `get_jobs` is the DASHBOARD query: it hides archived jobs by default, so a
   right-of-access export would have omitted every job older than 30 days.

These run the real `SupabaseClient` against `postgrest_double.FakeSupabase`,
which applies filters and reproduces the 416 (see test_postgrest_double.py),
and read the ZIP the endpoint actually returns.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db_client
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase

ME = "ee449fe1-7c97-4ba2-96ea-54bf2a1ce20a"
OTHER = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"


def _iso(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _job(user, job_id, days_ago, **extra):
    row = {
        "job_id": job_id, "user_id": user, "title": "Site Reliability Engineer",
        "company": "Acme", "description": "Run Kubernetes.", "source": "manual",
        "first_seen": _iso(days_ago), "application_status": "New",
        "match_score": 81, "score_tier": "A",
        "resume_s3_key": f"users/{user}/resumes/{job_id}_tailored.pdf",
        "embedding": [0.1] * 8,
    }
    row.update(extra)
    return row


@pytest.fixture
def fake():
    return FakeSupabase({
        "users": [
            {"id": ME, "email": "e2e@example.com", "name": "Live Run",
             "phone": "+353 1 234 5678", "location": "Dublin",
             "gdpr_deletion_requested_at": None},
            {"id": OTHER, "email": "other@example.com", "name": "Someone Else"},
        ],
        "user_resumes": [
            {"id": "r1", "user_id": ME, "resume_key": "default", "label": "My CV",
             "tex_content": "\\documentclass{article}", "created_at": _iso(1)},
            {"id": "r2", "user_id": OTHER, "resume_key": "default", "label": "Theirs",
             "tex_content": "x", "created_at": _iso(1)},
        ],
        "user_search_configs": [
            {"user_id": ME, "queries": ["SRE"], "locations": ["Dublin"]},
        ],
        "jobs": [
            _job(ME, "job-fresh", 0),
            # Archived by the dashboard's lifecycle (> 30 days, not engaged).
            # Still the user's data; Article 15 has no age limit.
            _job(ME, "job-old", 90),
            _job(OTHER, "job-theirs", 0),
        ],
        "application_timeline": [
            {"id": "t1", "user_id": ME, "job_id": "job-fresh", "status": "Applied",
             "notes": "sent", "created_at": _iso(0)},
            {"id": "t2", "user_id": OTHER, "job_id": "job-theirs", "status": "Applied",
             "notes": "", "created_at": _iso(0)},
        ],
        "resume_versions": [
            {"id": "v1", "user_id": ME, "job_id": "job-fresh", "version_number": 1,
             "resume_s3_key": f"users/{ME}/resumes/job-fresh_v1.pdf", "created_at": _iso(0)},
        ],
        "runs": [
            {"run_id": "run1", "user_id": ME, "run_date": "2026-10-09", "status": "completed"},
            {"run_id": "run2", "user_id": OTHER, "run_date": "2026-10-09", "status": "completed"},
        ],
    })


@pytest.fixture
def db(fake):
    with patch.object(db_client, "create_client", return_value=fake):
        return db_client.SupabaseClient("https://example.supabase.co", "service-key")


@pytest.fixture
def exported(db, monkeypatch):
    monkeypatch.setattr(app_module, "_db", db)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=ME, email="e2e@example.com")
    try:
        with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
            r = TestClient(app_module.app).get("/api/gdpr/export")
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    return {name: json.loads(zf.read(name)) for name in zf.namelist()}


def test_export_of_a_user_with_one_job_is_not_a_500(exported):
    assert "jobs.json" in exported


def test_jobs_are_rows_not_rows_and_a_count(exported):
    jobs = exported["jobs.json"]
    assert all(isinstance(j, dict) for j in jobs), jobs
    assert {j["job_id"] for j in jobs} == {"job-fresh", "job-old"}


def test_archived_jobs_are_exported_too(exported):
    """The dashboard hides jobs older than 30 days. An export is not the
    dashboard; leaving them out under-reports what we hold."""
    assert "job-old" in {j["job_id"] for j in exported["jobs.json"]}


def test_every_table_holding_the_users_rows_is_in_the_archive(exported):
    assert exported["profile.json"]["name"] == "Live Run"
    assert [r["label"] for r in exported["resumes.json"]] == ["My CV"]
    assert exported["search_config.json"]["queries"] == ["SRE"]
    assert [t["status"] for t in exported["timeline.json"]] == ["Applied"]
    assert [v["version_number"] for v in exported["resume_versions.json"]] == [1]
    assert [r["run_id"] for r in exported["runs.json"]] == ["run1"]


def test_nothing_belonging_to_another_user_is_exported(exported):
    blob = json.dumps(exported)
    assert OTHER not in blob
    assert "Someone Else" not in blob and "job-theirs" not in blob


def test_more_than_one_page_of_jobs_is_all_exported(fake, db):
    """The walk must not stop at the first page, nor 416 at the end of it."""
    from gdpr import export_user_data

    fake.tables["jobs"] = [_job(ME, f"j{i}", 0) for i in range(1234)]
    zf = zipfile.ZipFile(io.BytesIO(export_user_data(db, ME)))
    assert len(json.loads(zf.read("jobs.json"))) == 1234



def test_exactly_one_full_page_of_jobs_exports(fake, db):
    """The walk stops on a short page, so at exactly 1000 rows it asks for a
    second page starting at the end. Uncounted, that is `[]`; counted, it is
    the same 416 that took the export down."""
    from gdpr import export_user_data

    fake.tables["jobs"] = [_job(ME, f"j{i}", 0) for i in range(1000)]
    zf = zipfile.ZipFile(io.BytesIO(export_user_data(db, ME)))
    assert len(json.loads(zf.read("jobs.json"))) == 1000
