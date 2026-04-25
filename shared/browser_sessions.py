"""DynamoDB helpers and API Gateway Management API wrapper for cloud browser sessions.

All cloud-browser Lambdas (ws_connect / ws_disconnect / ws_route) and the
FastAPI apply endpoints use these helpers so the DDB schema is defined in
exactly one place.

Schema (table: naukribaba-browser-sessions, PK: session_id, GSI: user-sessions-index on user_id):
    session_id              (S, PK)
    user_id                 (S, GSI hash)
    current_job_id          (S)
    platform                (S)
    fargate_task_arn        (S)
    status                  (S)  -- starting | ready | filling | submitted | ended
    ws_connection_frontend  (S, optional)
    ws_connection_browser   (S, optional)
    created_at              (S, ISO-8601)
    last_activity_at        (N, unix ts)
    ttl                     (N, unix ts — DynamoDB TTL-enabled)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key as DDBKey

AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "naukribaba-browser-sessions")

_ALLOWED_ROLES = {"frontend", "browser"}
_ACTIVE_STATUSES = {"starting", "ready", "filling"}


def _table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(SESSIONS_TABLE)


def create_session(
    *,
    session_id: str,
    user_id: str,
    job_id: str,
    platform: str,
    fargate_task_arn: str,
    ttl_seconds: int = 1800,
) -> dict:
    now = int(time.time())
    item = {
        "session_id": session_id,
        "user_id": user_id,
        "current_job_id": job_id,
        "platform": platform,
        "fargate_task_arn": fargate_task_arn,
        "status": "starting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": now,
        "ttl": now + ttl_seconds,
    }
    _table().put_item(Item=item)
    return item


def get_session(session_id: str) -> Optional[dict]:
    resp = _table().get_item(Key={"session_id": session_id})
    return resp.get("Item")


def set_connection_id(session_id: str, *, role: str, connection_id: str) -> None:
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"role must be one of {_ALLOWED_ROLES}, got {role!r}")
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression=f"SET ws_connection_{role} = :c, last_activity_at = :t",
        ExpressionAttributeValues={":c": connection_id, ":t": int(time.time())},
    )


def clear_connection_id(session_id: str, *, role: str) -> None:
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"role must be one of {_ALLOWED_ROLES}, got {role!r}")
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression=f"REMOVE ws_connection_{role}",
    )


def update_status(session_id: str, status: str) -> None:
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET #s = :s, last_activity_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":t": int(time.time())},
    )


def find_active_session_for_user(user_id: str) -> Optional[dict]:
    """Returns the newest active session for `user_id`, or None."""
    resp = _table().query(
        IndexName="user-sessions-index",
        KeyConditionExpression=DDBKey("user_id").eq(user_id),
    )
    active = [i for i in resp.get("Items", []) if i.get("status") in _ACTIVE_STATUSES]
    if not active:
        return None
    active.sort(key=lambda i: i.get("last_activity_at", 0), reverse=True)
    return active[0]


def find_session_by_connection(connection_id: str) -> Optional[tuple[dict, str]]:
    """Scan for a session whose frontend OR browser connection id matches.

    Scan is acceptable at current scale (<=  tens of concurrent sessions,
    TTL 30 min). Revisit with a connection_id -> session_id pointer item
    (same table, PK=`conn#<id>`) when concurrent sessions cross ~100."""
    resp = _table().scan(
        FilterExpression=(
            "ws_connection_frontend = :c OR ws_connection_browser = :c"
        ),
        ExpressionAttributeValues={":c": connection_id},
    )
    items = resp.get("Items", [])
    if not items:
        return None
    s = items[0]
    role = "frontend" if s.get("ws_connection_frontend") == connection_id else "browser"
    return s, role


def post_to_connection(*, api_id: str, region: str, connection_id: str, data: bytes) -> None:
    """Send payload to a WebSocket connection via API Gateway Management API.

    Caller handles GoneException (stale connection id) — the right fallback
    varies by caller (ws_route wants to clear the slot; stop-session may
    want to ignore)."""
    endpoint = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"
    client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint, region_name=region)
    client.post_to_connection(ConnectionId=connection_id, Data=data)
