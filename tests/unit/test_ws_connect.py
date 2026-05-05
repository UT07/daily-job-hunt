"""Unit tests for the WebSocket $connect handler."""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SESSIONS_TABLE": "test-sessions",
    }):
        yield


def _event(*, authorization, session_qs, role_qs, connection_id="conn-abc"):
    headers = {"Authorization": authorization} if authorization is not None else {}
    qs = {}
    if session_qs is not None:
        qs["session"] = session_qs
    if role_qs is not None:
        qs["role"] = role_qs
    return {
        "headers": headers,
        "queryStringParameters": qs or None,
        "requestContext": {"connectionId": connection_id},
    }


def _token(*, session_id, role, user_id="user-1", ttl=60):
    from shared.ws_auth import issue_ws_token
    return issue_ws_token(user_id=user_id, session_id=session_id, role=role, ttl_seconds=ttl)


def test_rejects_missing_authorization():
    from lambdas.browser.ws_connect import handler
    resp = handler(_event(authorization=None, session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 401


def test_rejects_wrong_scheme():
    from lambdas.browser.ws_connect import handler
    resp = handler(_event(authorization="Basic foo", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 401


def test_rejects_missing_session_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs=None, role_qs="frontend"), None)
    assert resp["statusCode"] == 400


def test_rejects_missing_role_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs=None), None)
    assert resp["statusCode"] == 400


def test_rejects_invalid_role_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="attacker"), None)
    assert resp["statusCode"] == 400


def test_rejects_session_mismatch_between_token_and_query():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-token", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-query", role_qs="frontend"), None)
    assert resp["statusCode"] == 403


def test_rejects_frontend_token_used_with_role_browser():
    """Critical: token audience must match the role query param."""
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="browser"), None)
    assert resp["statusCode"] == 401


def test_rejects_token_for_unknown_session():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    with patch("shared.browser_sessions.get_session", return_value=None):
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 404


def test_rejects_when_token_user_does_not_match_session_user():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend", user_id="user-A")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-B"}):
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 403


def test_accepts_frontend_and_registers_connection():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend", user_id="user-1")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-1"}), \
         patch("shared.browser_sessions.set_connection_id") as m_set:
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 200
    m_set.assert_called_once_with("sess-1", role="frontend", connection_id="conn-abc")


def test_accepts_browser_and_registers_connection():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="browser", user_id="user-1")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-1"}), \
         patch("shared.browser_sessions.set_connection_id") as m_set:
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="browser"), None)
    assert resp["statusCode"] == 200
    m_set.assert_called_once_with("sess-1", role="browser", connection_id="conn-abc")


def test_connect_accepts_token_via_sec_websocket_protocol_header():
    """Browser WebSocket can't set Authorization; the only way to pass the
    token is via the Sec-WebSocket-Protocol subprotocol header.

    Format: 'naukribaba-auth.<token>'. Lambda must extract the suffix and
    treat it as a Bearer token.
    """
    from lambdas.browser.ws_connect import handler
    from shared.ws_auth import issue_ws_token

    token = issue_ws_token(
        session_id="sess-1", user_id="user-1", role="frontend"
    )

    event = {
        "requestContext": {"connectionId": "conn-A"},
        "queryStringParameters": {"session": "sess-1", "role": "frontend"},
        "headers": {
            "Sec-WebSocket-Protocol": f"naukribaba-auth.{token}",
            # No Authorization header — must succeed via subprotocol alone
        },
    }

    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "status": "starting",
    }), patch("shared.browser_sessions.set_connection_id"):
        result = handler(event, None)

    assert result["statusCode"] == 200
    # Server must echo the chosen subprotocol back per RFC 6455
    assert result["headers"]["Sec-WebSocket-Protocol"] == "naukribaba-auth"
