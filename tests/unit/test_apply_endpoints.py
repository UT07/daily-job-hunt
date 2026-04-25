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
    existing = {"session_id": "warm-1", "status": "ready", "user_id": "user-1", "current_job_id": "j1"}
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


# ---- Stop Session ----

def test_stop_session_404_unknown(client):
    c, _ = client
    with patch("shared.browser_sessions.get_session", return_value=None):
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-x"})
    assert r.status_code == 404


def test_stop_session_403_wrong_user(client):
    c, _ = client
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "someone-else", "fargate_task_arn": "arn:x",
    }):
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 403


def test_stop_session_happy_path(client):
    c, _ = client
    ecs = MagicMock()
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "fargate_task_arn": "arn:x",
    }), patch("boto3.client", return_value=ecs), \
         patch("shared.browser_sessions.update_status") as m_status:
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    ecs.stop_task.assert_called_once()
    m_status.assert_called_once_with("sess-1", "ended")


def test_stop_session_still_marks_ended_if_ecs_fails(client):
    c, _ = client
    ecs = MagicMock()
    ecs.stop_task.side_effect = Exception("ECS down")
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "fargate_task_arn": "arn:x",
    }), patch("boto3.client", return_value=ecs), \
         patch("shared.browser_sessions.update_status") as m_status:
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 200
    m_status.assert_called_once_with("sess-1", "ended")


# ---- Record Application (idempotent) ----

def test_record_404_when_job_missing(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.post("/api/apply/record", json={"session_id": "s1", "job_id": "j-gone"})
    assert r.status_code == 404


def test_record_inserts_application_with_cloud_browser_method(client):
    c, db = client
    apps = MagicMock()
    # idempotency check — no existing row
    chain = apps.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[])
    # insert path
    apps.insert.return_value = apps
    apps.execute.return_value = MagicMock(data=[{"id": "app-NEW"}])
    jobs = MagicMock()
    jobs.update.return_value = jobs; jobs.eq.return_value = jobs
    jobs.execute.return_value = MagicMock()
    timeline = MagicMock()
    timeline.insert.return_value = timeline
    timeline.execute.return_value = MagicMock(data=[{"id": "t-1"}])

    def router(name):
        return {"applications": apps, "jobs": jobs, "application_timeline": timeline}.get(name, MagicMock())
    db.client.table.side_effect = router

    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/record", json={
            "session_id": "sess-1", "job_id": "j1",
            "confirmation_screenshot_key": "c.png",
            "form_fields_detected": 12, "form_fields_filled": 11,
        })
    assert r.status_code == 200
    assert r.json() == {"status": "recorded", "application_id": "app-NEW", "idempotent": False}
    inserted = apps.insert.call_args.args[0]
    assert inserted["submission_method"] == "cloud_browser"
    assert inserted["browser_session_id"] == "sess-1"
    assert inserted["form_fields_detected"] == 12
    assert inserted["form_fields_filled"] == 11


def test_record_is_idempotent_on_duplicate(client):
    """If canonical_hash already has an active applications row, return
    existing id instead of double-inserting."""
    c, db = client
    apps = MagicMock()
    chain = apps.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "app-EXISTING", "status": "submitted"}])
    apps.insert.return_value = apps
    apps.execute.side_effect = AssertionError("insert must NOT be called on duplicate")

    db.client.table.side_effect = lambda name: apps if name == "applications" else MagicMock()

    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/record", json={"session_id": "sess-1", "job_id": "j1"})
    assert r.status_code == 200
    assert r.json() == {"status": "recorded", "application_id": "app-EXISTING", "idempotent": True}


# ---- Review fixes (post-implementation) ----

def test_start_session_409_when_active_session_for_different_job(client):
    """Reusing a warm session across different job_ids would mean Fargate
    is pointed at the wrong apply_url. Reject with 409."""
    c, db = client
    db.get_user.return_value = _complete_user()
    existing = {"session_id": "warm-A", "status": "ready", "user_id": "user-1", "current_job_id": "job-A"}
    with patch("shared.load_job.load_job", return_value=_job_row(job_id="job-B")), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=existing):
        r = c.post("/api/apply/start-session", json={"job_id": "job-B"})
    assert r.status_code == 409
    assert "session_active_for_different_job" in r.json()["detail"]


def test_start_session_reuses_warm_session_for_same_job(client):
    """Same-job warm reuse must still work."""
    c, db = client
    db.get_user.return_value = _complete_user()
    existing = {"session_id": "warm-1", "status": "ready", "user_id": "user-1", "current_job_id": "j1"}
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=existing):
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "warm-1"
    assert r.json()["reused"] is True


def test_eligibility_skips_canonical_hash_check_when_empty(client):
    """A job without canonical_hash must NOT match other no-hash applications."""
    c, db = client
    # If the SELECT is consulted, it would collapse across no-hash rows.
    # Mock the .execute() to RAISE so we know it wasn't called.
    chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.side_effect = AssertionError("must not query when canonical empty")
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row(canonical_hash=None)):
        r = c.get("/api/apply/eligibility/j1")
    # Should reach happy path, not match a stale no-hash row
    assert r.status_code == 200
    assert r.json() == {
        "eligible": True, "platform": "greenhouse",
        "board_token": "acme", "posting_id": "12345",
    }


def test_record_skips_canonical_hash_idempotency_when_empty(client):
    """Without canonical_hash, fall through to insert — don't false-positive
    on other jobs missing a hash."""
    c, db = client
    apps = MagicMock()
    # Idempotency SELECT must NOT be called
    apps.select.side_effect = AssertionError("must not check idempotency when canonical empty")
    apps.insert.return_value = apps
    apps.execute.return_value = MagicMock(data=[{"id": "app-NEW2"}])
    db.client.table.side_effect = lambda name: apps if name == "applications" else MagicMock()

    with patch("shared.load_job.load_job", return_value=_job_row(canonical_hash=None)):
        r = c.post("/api/apply/record", json={"session_id": "s1", "job_id": "j1"})
    assert r.status_code == 200
    assert r.json()["application_id"] == "app-NEW2"
    assert r.json()["idempotent"] is False
