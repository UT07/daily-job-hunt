"""Unit tests for shared.browser_sessions."""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SESSIONS_TABLE": "test-sessions", "AWS_REGION": "eu-west-1"}):
        yield


def _mock_table():
    table = MagicMock()
    ddb = MagicMock()
    ddb.Table.return_value = table
    return ddb, table


def test_create_session_writes_ddb_row_with_ttl():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import create_session
        row = create_session(
            session_id="sess-123",
            user_id="user-1",
            job_id="job-9",
            platform="greenhouse",
            fargate_task_arn="arn:aws:ecs:...:task/xyz",
            ttl_seconds=1800,
        )
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["session_id"] == "sess-123"
    assert item["user_id"] == "user-1"
    assert item["current_job_id"] == "job-9"
    assert item["platform"] == "greenhouse"
    assert item["fargate_task_arn"].startswith("arn:aws:ecs")
    assert item["status"] == "starting"
    assert item["ttl"] > 0
    assert row == item


def test_get_session_returns_none_when_missing():
    ddb, table = _mock_table()
    table.get_item.return_value = {}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import get_session
        assert get_session("sess-missing") is None


def test_get_session_returns_item_when_present():
    ddb, table = _mock_table()
    table.get_item.return_value = {"Item": {"session_id": "sess-1", "status": "ready"}}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import get_session
        got = get_session("sess-1")
    assert got == {"session_id": "sess-1", "status": "ready"}


def test_set_connection_id_frontend_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import set_connection_id
        set_connection_id("sess-1", role="frontend", connection_id="abc123")
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"session_id": "sess-1"}
    assert "ws_connection_frontend" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeValues"][":c"] == "abc123"


def test_set_connection_id_browser_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import set_connection_id
        set_connection_id("sess-1", role="browser", connection_id="abc456")
    kwargs = table.update_item.call_args.kwargs
    assert "ws_connection_browser" in kwargs["UpdateExpression"]


def test_set_connection_id_rejects_unknown_role():
    with patch("boto3.resource"):
        from shared.browser_sessions import set_connection_id
        with pytest.raises(ValueError, match="role must be"):
            set_connection_id("sess-1", role="attacker", connection_id="x")


def test_clear_connection_id_removes_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import clear_connection_id
        clear_connection_id("sess-1", role="browser")
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["UpdateExpression"] == "REMOVE ws_connection_browser"


def test_find_active_session_for_user_uses_gsi():
    ddb, table = _mock_table()
    table.query.return_value = {"Items": [{"session_id": "sess-1", "status": "ready", "last_activity_at": 100}]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_active_session_for_user
        got = find_active_session_for_user("user-1")
    assert got is not None
    assert got["session_id"] == "sess-1"
    kwargs = table.query.call_args.kwargs
    assert kwargs["IndexName"] == "user-sessions-index"


def test_find_active_session_returns_none_when_none_active():
    ddb, table = _mock_table()
    table.query.return_value = {"Items": [{"session_id": "sess-1", "status": "ended"}]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_active_session_for_user
        assert find_active_session_for_user("user-1") is None


def test_find_session_by_connection_returns_session_and_role_for_frontend_match():
    ddb, table = _mock_table()
    session = {"session_id": "sess-1", "ws_connection_frontend": "conn-A", "status": "ready"}
    table.scan.return_value = {"Items": [session]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_session_by_connection
        result = find_session_by_connection("conn-A")
    assert result is not None
    returned_session, role = result
    assert returned_session == session
    assert role == "frontend"
    kwargs = table.scan.call_args.kwargs
    assert kwargs["FilterExpression"] == (
        "ws_connection_frontend = :c OR ws_connection_browser = :c"
    )
    assert kwargs["ExpressionAttributeValues"] == {":c": "conn-A"}


def test_find_session_by_connection_returns_session_and_role_for_browser_match():
    ddb, table = _mock_table()
    session = {"session_id": "sess-2", "ws_connection_browser": "conn-B", "status": "filling"}
    table.scan.return_value = {"Items": [session]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_session_by_connection
        result = find_session_by_connection("conn-B")
    assert result is not None
    returned_session, role = result
    assert returned_session == session
    assert role == "browser"
    kwargs = table.scan.call_args.kwargs
    assert kwargs["FilterExpression"] == (
        "ws_connection_frontend = :c OR ws_connection_browser = :c"
    )
    assert kwargs["ExpressionAttributeValues"] == {":c": "conn-B"}


def test_find_session_by_connection_returns_none_when_no_match():
    ddb, table = _mock_table()
    table.scan.return_value = {"Items": []}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_session_by_connection
        result = find_session_by_connection("conn-Z")
    assert result is None
    kwargs = table.scan.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"] == {":c": "conn-Z"}


def test_post_to_connection_uses_management_api():
    mgmt = MagicMock()
    with patch("boto3.client", return_value=mgmt) as m_client:
        from shared.browser_sessions import post_to_connection
        post_to_connection(
            api_id="abc123",
            region="eu-west-1",
            connection_id="conn-xyz",
            data=b'{"action":"click","x":10,"y":20}',
        )
    m_client.assert_called_once()
    client_kwargs = m_client.call_args.kwargs
    assert client_kwargs["endpoint_url"] == "https://abc123.execute-api.eu-west-1.amazonaws.com/prod"
    mgmt.post_to_connection.assert_called_once_with(
        ConnectionId="conn-xyz",
        Data=b'{"action":"click","x":10,"y":20}',
    )
