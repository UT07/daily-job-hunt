"""Unit tests for /api/apply/* endpoints."""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SESSIONS_TABLE": "test-sessions",
        "CLUSTER_ARN": "arn:aws:ecs:eu-west-1:1:cluster/c",
        "TASK_DEF": "td",
        "SECURITY_GROUP": "sg-1",
        "BROWSER_SUBNET_IDS": "subnet-a,subnet-b",
        "BROWSER_WS_URL": "wss://ws.example/prod",
        "WEBSOCKET_API_ID": "ws-abc",
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


def _job_row(**over):
    row = {
        "job_id": "j1", "user_id": "user-1", "title": "Backend",
        "company": "Acme", "apply_platform": "greenhouse",
        "apply_board_token": "acme", "apply_posting_id": "12345",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/12345",
        "canonical_hash": "h-abc",
        "resume_s3_key": "users/user-1/resumes/v1.pdf",
        "resume_version": 1, "job_hash": "jh-1",
    }
    row.update(over)
    return row


def _complete_user():
    return {
        "id": "user-1",
        "first_name": "U", "last_name": "S", "email": "u@e.com",
        "phone": "+353851234567", "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp1g",
        "work_authorizations": {"IE": "stamp1g"},
        "default_referral_source": "LinkedIn",
        "notice_period_text": "2 weeks",
    }


def _no_existing_apps(db):
    chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[])


def _existing_app(db, row):
    chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[row])


# ---- Eligibility ----

def test_eligibility_job_not_found(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.get("/api/apply/eligibility/j1")
    assert r.status_code == 404


def test_eligibility_platform_not_supported(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}


def test_eligibility_no_resume(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(resume_s3_key=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json()["reason"] == "no_resume"


def test_eligibility_already_applied(client):
    c, db = client
    _existing_app(db, {"id": "app-1", "status": "submitted", "submitted_at": "2026-04-24T10:00:00Z"})
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json()["reason"] == "already_applied"
    assert r.json()["application_id"] == "app-1"


def test_eligibility_profile_incomplete(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    body = r.json()
    assert body["reason"] == "profile_incomplete"
    assert "phone" in body["missing_required_fields"]


def test_eligibility_happy_path(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {
        "eligible": True, "platform": "greenhouse",
        "board_token": "acme", "posting_id": "12345",
    }
