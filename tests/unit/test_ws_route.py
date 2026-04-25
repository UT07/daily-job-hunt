import json
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SESSIONS_TABLE": "test-sessions",
        "WEBSOCKET_API_ID": "ws-abc",
        "AWS_REGION": "eu-west-1",
    }):
        yield


def _event(*, connection_id, body):
    return {"requestContext": {"connectionId": connection_id}, "body": body}


def test_relays_frontend_message_to_browser_peer():
    from lambdas.browser.ws_route import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    payload = json.dumps({"action": "click", "x": 10, "y": 20})
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-fe", body=payload), None)
    assert resp["statusCode"] == 200
    call = m_post.call_args.kwargs
    assert call["connection_id"] == "conn-br"
    assert call["data"] == payload.encode("utf-8")


def test_relays_browser_message_to_frontend_peer():
    from lambdas.browser.ws_route import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    payload = json.dumps({"action": "status", "status": "ready"})
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "browser")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-br", body=payload), None)
    assert resp["statusCode"] == 200
    assert m_post.call_args.kwargs["connection_id"] == "conn-fe"


def test_drops_when_peer_absent():
    from lambdas.browser.ws_route import handler
    session = {"session_id": "sess-1", "ws_connection_frontend": "conn-fe"}
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-fe", body='{"action":"click"}'), None)
    assert resp["statusCode"] == 200
    m_post.assert_not_called()


def test_drops_when_no_session_found():
    from lambdas.browser.ws_route import handler
    with patch("shared.browser_sessions.find_session_by_connection", return_value=None), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-ghost", body='{"a":1}'), None)
    assert resp["statusCode"] == 200
    m_post.assert_not_called()


def test_clears_peer_on_gone_exception():
    from botocore.exceptions import ClientError
    from lambdas.browser.ws_route import handler

    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    gone = ClientError({"Error": {"Code": "GoneException"}}, "PostToConnection")
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection", side_effect=gone), \
         patch("shared.browser_sessions.clear_connection_id") as m_clear:
        resp = handler(_event(connection_id="conn-fe", body='{"a":1}'), None)
    assert resp["statusCode"] == 200
    m_clear.assert_called_once_with("sess-1", role="browser")


def test_rejects_oversized_body():
    from lambdas.browser.ws_route import handler
    big = "x" * 200_000
    resp = handler(_event(connection_id="any", body=big), None)
    assert resp["statusCode"] == 413
