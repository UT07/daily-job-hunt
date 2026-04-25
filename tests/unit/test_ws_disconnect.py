import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SESSIONS_TABLE": "test-sessions",
        "CLUSTER_ARN": "arn:aws:ecs:eu-west-1:1:cluster/c",
    }):
        yield


def _event(connection_id="conn-disc"):
    return {"requestContext": {"connectionId": connection_id}}


def test_noop_when_connection_matches_no_session():
    from lambdas.browser.ws_disconnect import handler
    with patch("shared.browser_sessions.find_session_by_connection", return_value=None):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200


def test_clears_frontend_leaves_browser_alive():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "ws_connection_browser": "conn-br",
    }
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id") as m_clear, \
         patch("shared.browser_sessions.update_status") as m_status, \
         patch("boto3.client") as m_boto:
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
    m_clear.assert_called_once_with("sess-1", role="frontend")
    m_status.assert_not_called()
    m_boto.return_value.stop_task.assert_not_called()


def test_stops_fargate_when_both_sides_disconnected():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "fargate_task_arn": "arn:ecs:task/xyz",
    }
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id"), \
         patch("shared.browser_sessions.update_status") as m_status, \
         patch("boto3.client") as m_boto:
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
    m_status.assert_called_once_with("sess-1", "ended")
    m_boto.return_value.stop_task.assert_called_once()


def test_swallows_stop_task_failure():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "fargate_task_arn": "arn:ecs:task/xyz",
    }
    ecs = MagicMock()
    ecs.stop_task.side_effect = Exception("ECS blew up")
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id"), \
         patch("shared.browser_sessions.update_status"), \
         patch("boto3.client", return_value=ecs):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200


def test_top_level_never_throws():
    from lambdas.browser.ws_disconnect import handler
    with patch("shared.browser_sessions.find_session_by_connection", side_effect=RuntimeError("boom")):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
