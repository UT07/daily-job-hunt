"""Contract test: start-session → ws_connect (frontend) → ws_connect (browser)
→ ws_route → record → stop-session. All AWS mocked."""
import json
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


def test_end_to_end_apply_flow():
    import app as app_module
    from auth import AuthUser, get_current_user
    from lambdas.browser import ws_connect, ws_route

    db = MagicMock()
    db.get_user.return_value = {
        "id": "user-1", "first_name": "U", "last_name": "S", "email": "u@e.com",
        "phone": "+353851234567", "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp1g", "work_authorizations": {"IE": "ok"},
        "default_referral_source": "LinkedIn", "notice_period_text": "2w",
    }
    apps_chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    apps_chain.execute.return_value = MagicMock(data=[])
    db.client.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "app-NEW"}],
    )

    app_module._db = db
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@e.com",
    )

    sessions_store: dict = {}
    posted: list = []

    def fake_create_session(**kw):
        # Mirror real shared.browser_sessions.create_session: stores
        # `current_job_id`, not the `job_id` kwarg name.
        row = {**kw, "status": "starting", "current_job_id": kw.get("job_id")}
        sessions_store[kw["session_id"]] = row
        return row

    def fake_get_session(sid): return sessions_store.get(sid)
    def fake_set_conn(sid, *, role, connection_id): sessions_store[sid][f"ws_connection_{role}"] = connection_id
    def fake_update_status(sid, status): sessions_store[sid]["status"] = status
    def fake_find_active(uid):
        for r in sessions_store.values():
            if r.get("user_id") == uid and r.get("status") in ("starting", "ready"):
                return r
        return None
    def fake_post(**kw): posted.append(kw)
    def fake_find_by_conn(cid):
        for r in sessions_store.values():
            if r.get("ws_connection_frontend") == cid:
                return r, "frontend"
            if r.get("ws_connection_browser") == cid:
                return r, "browser"
        return None

    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task:abc"}], "failures": []}

    with patch("shared.browser_sessions.create_session", side_effect=fake_create_session), \
         patch("shared.browser_sessions.get_session", side_effect=fake_get_session), \
         patch("shared.browser_sessions.set_connection_id", side_effect=fake_set_conn), \
         patch("shared.browser_sessions.find_active_session_for_user", side_effect=fake_find_active), \
         patch("shared.browser_sessions.update_status", side_effect=fake_update_status), \
         patch("shared.browser_sessions.post_to_connection", side_effect=fake_post), \
         patch("shared.browser_sessions.find_session_by_connection", side_effect=fake_find_by_conn), \
         patch("boto3.client", return_value=ecs), \
         patch("shared.load_job.load_job", return_value={
             "job_id": "j1", "user_id": "user-1", "title": "Backend",
             "apply_platform": "greenhouse",
             "apply_url": "https://boards.greenhouse.io/x/y",
             "apply_board_token": "x", "apply_posting_id": "12345",
             "canonical_hash": "h-abc", "resume_s3_key": "u/r.pdf",
             "resume_version": 1, "job_hash": "jh-1",
         }):

        client = TestClient(app_module.app)

        # 1. start-session
        r = client.post("/api/apply/start-session", json={"job_id": "j1"})
        assert r.status_code == 200, r.text
        body = r.json()
        session_id = body["session_id"]
        frontend_token = body["ws_token"]

        # Derive browser token as Fargate would (in prod, it's in WS_TOKEN env)
        from shared.ws_auth import issue_ws_token
        browser_token = issue_ws_token(user_id="user-1", session_id=session_id, role="browser")

        # 2. ws_connect (frontend)
        fe_event = {
            "headers": {"Authorization": f"Bearer {frontend_token}"},
            "queryStringParameters": {"session": session_id, "role": "frontend"},
            "requestContext": {"connectionId": "conn-fe"},
        }
        assert ws_connect.handler(fe_event, None)["statusCode"] == 200

        # 3. ws_connect (browser)
        br_event = {
            "headers": {"Authorization": f"Bearer {browser_token}"},
            "queryStringParameters": {"session": session_id, "role": "browser"},
            "requestContext": {"connectionId": "conn-br"},
        }
        assert ws_connect.handler(br_event, None)["statusCode"] == 200

        # 4. ws_route frontend → browser
        route_event = {
            "requestContext": {"connectionId": "conn-fe"},
            "body": json.dumps({"action": "click", "x": 10, "y": 20}),
        }
        assert ws_route.handler(route_event, None)["statusCode"] == 200
        assert any(p["connection_id"] == "conn-br" for p in posted)

        # 5. record
        r = client.post("/api/apply/record", json={
            "session_id": session_id, "job_id": "j1",
            "confirmation_screenshot_key": "c.png",
            "form_fields_detected": 5, "form_fields_filled": 5,
        })
        assert r.status_code == 200
        assert r.json()["application_id"] == "app-NEW"
        assert r.json()["idempotent"] is False

        # 6. stop-session
        r = client.post("/api/apply/stop-session", json={"session_id": session_id})
        assert r.status_code == 200
        assert sessions_store[session_id]["status"] == "ended"

    app_module.app.dependency_overrides.clear()
