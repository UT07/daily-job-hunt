"""Bulk /api/pipeline/re-tailor must handle manual rows and report truthfully.

It selected only `job_id, job_hash` and did `job['job_hash'][:12]`. Manual
rows have job_hash NULL, so every one raised TypeError inside the loop, was
counted as an error, and the response still opened with "Started re-tailoring
N jobs". Manual rows are tailored under canonical_hash (shared.tailor_hash).
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase


def _job(job_id, job_hash, chash, score=90, user="alice"):
    return {"job_id": job_id, "user_id": user, "job_hash": job_hash, "canonical_hash": chash,
            "score_tier": "S", "match_score": score, "is_expired": False, "resume_s3_url": None}


@pytest.fixture
def env(monkeypatch):
    db = FakeSupabase({
        "jobs": [
            _job("j-scraped", "h-scraped", "c-scraped", 99),
            _job("c-manual", None, "c-manual", 98),         # manual, posting stored
            _job("c-nopost", None, "c-nopost", 97),         # manual, posting never stored
            _job("j-nohash", None, None, 96),               # nothing to tailor under
            _job("c-manual", None, "c-manual", 95, user="bob"),  # another user's
        ],
        "jobs_raw": [{"job_hash": "h-scraped"}, {"job_hash": "c-manual"}],
    })
    monkeypatch.setattr(app_module, "_db", db)
    sfn = MagicMock()
    monkeypatch.setattr(app_module, "_get_sfn", lambda: sfn)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id="alice", email="a@x")
    with patch.dict(os.environ, {"SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:r:1:stateMachine:sj"}):
        yield TestClient(app_module.app), sfn
    app_module.app.dependency_overrides.clear()


def _inputs(sfn):
    import json
    return [json.loads(c.kwargs["input"]) for c in sfn.start_execution.call_args_list]


def test_manual_rows_start_under_their_canonical_hash(env):
    client, sfn = env
    r = client.post("/api/pipeline/re-tailor", json={"tier": "S"})
    assert r.status_code == 202, r.text
    started = {(i["job_id"], i["job_hash"]) for i in _inputs(sfn)}
    assert started == {("j-scraped", "h-scraped"), ("c-manual", "c-manual")}
    assert all(i["user_id"] == "alice" for i in _inputs(sfn))
    body = r.json()
    assert body["started"] == 2
    assert body["failed"] == 0
    assert body["skipped"] == 2
    assert {s["job_id"] for s in body["skipped_jobs"]} == {"c-nopost", "j-nohash"}


def test_a_failed_start_is_reported_as_failed_not_started(env):
    client, sfn = env
    sfn.start_execution.side_effect = [{"executionArn": "x"}, RuntimeError("throttled")]
    body = client.post("/api/pipeline/re-tailor", json={"tier": "S"}).json()
    assert body["started"] == 1
    assert body["failed"] == 1
    assert "1 failed" in body["message"]
    assert body["message"].startswith("Started re-tailoring 1 of 4")


def test_nothing_started_does_not_say_started(env):
    client, sfn = env
    sfn.start_execution.side_effect = RuntimeError("AccessDenied")
    body = client.post("/api/pipeline/re-tailor", json={"tier": "S"}).json()
    assert body["started"] == 0 and body["failed"] == 2
    assert not body["message"].startswith("Started re-tailoring 2")
