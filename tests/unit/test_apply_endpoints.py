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


# ---- Preview (minimal, Plan 3b fills AI) ----

def test_preview_job_not_found(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 404


def test_preview_returns_ineligible_for_unsupported_platform(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 200
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}


def test_preview_returns_already_applied(client):
    """Preview must apply the SAME eligibility gates as /eligibility."""
    c, db = client
    _existing_app(db, {"id": "app-1", "status": "submitted"})
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    assert r.json()["reason"] == "already_applied"


def test_preview_returns_profile_incomplete(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    assert r.json()["reason"] == "profile_incomplete"


def test_preview_happy_path_returns_snapshot_without_ai_answers(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    body = r.json()
    assert r.status_code == 200
    assert body["eligible"] is True
    assert body["job"]["job_id"] == "j1"
    assert body["profile"]["first_name"] == "U"
    assert body["resume"]["s3_key"] == "users/user-1/resumes/v1.pdf"
    assert body["answers_generated"] is False
    assert body["answers"] == []
    assert body["questions"] == []


# ---- Start Session ----

def test_start_session_rejects_incomplete_profile(client):
    c, db = client
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 412
    assert "profile_incomplete" in r.json()["detail"]


def test_start_session_reuses_warm_session(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    existing = {"session_id": "warm-1", "status": "ready", "user_id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=existing), \
         patch("shared.browser_sessions.create_session") as m_create, \
         patch("boto3.client") as m_boto:
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "warm-1"
    assert body["reused"] is True
    m_create.assert_not_called()
    m_boto.return_value.run_task.assert_not_called()
    from shared.ws_auth import verify_ws_token
    claims = verify_ws_token(body["ws_token"], expected_role="frontend")
    assert claims["session"] == "warm-1"


def test_start_session_launches_fargate_with_browser_token_in_env(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    ecs = MagicMock()
    ecs.run_task.return_value = {
        "tasks": [{"taskArn": "arn:aws:ecs:eu-west-1:1:task/abc"}],
        "failures": [],
    }
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=None), \
         patch("shared.browser_sessions.create_session") as m_create, \
         patch("boto3.client", return_value=ecs):
        m_create.return_value = {"session_id": "sess-new", "user_id": "user-1"}
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 200
    body = r.json()
    task_kwargs = ecs.run_task.call_args.kwargs
    env = {e["name"]: e["value"] for e in task_kwargs["overrides"]["containerOverrides"][0]["environment"]}
    assert env["JOB_ID"] == "j1"
    assert env["USER_ID"] == "user-1"
    assert env["PLATFORM"] == "greenhouse"
    from shared.ws_auth import verify_ws_token
    # WS_TOKEN in Fargate env must be BROWSER-audience
    verify_ws_token(env["WS_TOKEN"], expected_role="browser")
    # Token returned in response must be FRONTEND-audience
    verify_ws_token(body["ws_token"], expected_role="frontend")
    assert task_kwargs["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-a", "subnet-b"]


def test_start_session_503_when_ecs_returns_failures(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY"}]}
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=None), \
         patch("boto3.client", return_value=ecs):
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 503
