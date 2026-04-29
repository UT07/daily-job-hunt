"""Unit tests for /api/pipeline/run-single endpoint (run_single_job).

Covers Bug 6: the SFN state machine references $.job_hash, $.user_id, and
$.skip_scoring, so the SFN input dict built by run_single_job must include
all of those keys. Without job_hash, ScoreSingleJob's
States.Array($.job_hash) fails immediately and the execution is FAILED.
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:eu-west-1:1:stateMachine:single",
        "AWS_REGION": "eu-west-1",
    }):
        yield


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    monkeypatch.setattr(app_module, "_db", db)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com",
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def _stub_sfn():
    sfn = MagicMock()
    sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:eu-west-1:1:execution:single:abc",
        "startDate": datetime.now(timezone.utc),
    }
    return sfn


# ---- Bug 6 regression tests ----


def test_run_single_job_sfn_input_includes_job_hash(client):
    """The SFN input dict must include job_hash; the state machine reads $.job_hash."""
    c, _ = client
    sfn = _stub_sfn()

    payload = {
        "job_description": "Build scalable backend services with Python and AWS. " * 5,
        "job_title": "Senior Backend Engineer",
        "company": "TechCorp",
        "resume_type": "sre_devops",
    }

    with patch("app._get_sfn", return_value=sfn), \
         patch("app._db", MagicMock()):
        resp = c.post("/api/pipeline/run-single", json=payload)

    assert resp.status_code == 202, resp.text
    sfn.start_execution.assert_called_once()
    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert "job_hash" in sfn_input, (
        f"job_hash missing from SFN input — state machine would fail at "
        f"States.Array($.job_hash). Got keys: {sorted(sfn_input.keys())}"
    )
    assert sfn_input["job_hash"], "job_hash must be a non-empty string"


def test_run_single_job_sfn_input_has_all_state_machine_fields(client):
    """SFN input dict must contain every key referenced via $.foo in the state machine.

    The SingleJobPipelineStateMachine references $.skip_scoring (Choice),
    $.user_id (Pass + ScoreSingleJob params), and $.job_hash (multiple states).
    """
    c, _ = client
    sfn = _stub_sfn()

    payload = {
        "job_description": "Long enough job description content here. " * 5,
        "job_title": "Engineer",
        "company": "Acme",
        "resume_type": "sre_devops",
    }

    with patch("app._get_sfn", return_value=sfn), \
         patch("app._db", MagicMock()):
        resp = c.post("/api/pipeline/run-single", json=payload)

    assert resp.status_code == 202
    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    for key in ("user_id", "job_hash", "skip_scoring"):
        assert key in sfn_input, f"SFN input missing required key {key!r}; got {sorted(sfn_input.keys())}"

    assert sfn_input["user_id"] == "user-1"
    assert sfn_input["skip_scoring"] is False


def test_run_single_job_job_hash_matches_canonical_hash(client):
    """The job_hash in SFN input must match canonical_hash(company, title, description)."""
    from utils.canonical_hash import canonical_hash

    c, _ = client
    sfn = _stub_sfn()

    company = "TechCorp"
    title = "Senior Backend Engineer"
    description = "Build scalable backend services with Python and AWS. " * 5

    payload = {
        "job_description": description,
        "job_title": title,
        "company": company,
        "resume_type": "sre_devops",
    }

    expected_hash = canonical_hash(company, title, description)

    with patch("app._get_sfn", return_value=sfn), \
         patch("app._db", MagicMock()):
        resp = c.post("/api/pipeline/run-single", json=payload)

    assert resp.status_code == 202
    sfn_input = json.loads(sfn.start_execution.call_args.kwargs["input"])
    assert sfn_input["job_hash"] == expected_hash


def test_run_single_job_upserts_jobs_raw_before_starting_sfn(client):
    """ScoreBatch reads from jobs_raw by job_hash, so the row must exist before SFN starts.

    Bug 6 root cause is two-fold: missing job_hash AND no jobs_raw row. Verify
    that the endpoint upserts into jobs_raw with the canonical hash so
    ScoreSingleJob -> ScoreBatchFunction can find the job.
    """
    c, _ = client
    sfn = _stub_sfn()

    db = MagicMock()
    table = MagicMock()
    db.client.table.return_value = table
    table.upsert.return_value.execute.return_value = MagicMock(data=[{"job_hash": "x"}])

    payload = {
        "job_description": "Build scalable backend services with Python. " * 5,
        "job_title": "Backend Engineer",
        "company": "TechCorp",
        "resume_type": "sre_devops",
    }

    with patch("app._get_sfn", return_value=sfn), \
         patch("app._db", db):
        resp = c.post("/api/pipeline/run-single", json=payload)

    assert resp.status_code == 202
    # jobs_raw must have been upserted before sfn.start_execution
    upsert_calls = [
        call for call in db.client.table.call_args_list
        if call.args and call.args[0] == "jobs_raw"
    ]
    assert upsert_calls, (
        "Expected an upsert into jobs_raw before SFN starts so ScoreBatch can find the job. "
        f"Tables touched: {[c.args[0] for c in db.client.table.call_args_list if c.args]}"
    )


def test_run_single_job_returns_poll_url_with_execution_id(client):
    """Sanity check: response shape matches existing /api/pipeline/run."""
    c, _ = client
    sfn = _stub_sfn()

    payload = {
        "job_description": "Long enough job description here. " * 5,
        "job_title": "Engineer",
        "company": "Acme",
        "resume_type": "sre_devops",
    }
    with patch("app._get_sfn", return_value=sfn), \
         patch("app._db", MagicMock()):
        resp = c.post("/api/pipeline/run-single", json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert "executionArn" in body
    assert "pollUrl" in body
    assert body["pollUrl"].startswith("/api/pipeline/status/")
