"""WebSocket $connect handler.

Matches Plan 2's client contract (browser/browser_session.py:410):
  wss://.../prod?session={session_id}&role={frontend|browser}
  Authorization: Bearer <ws_token>"""

from __future__ import annotations

import logging

import shared.browser_sessions as browser_sessions
from shared.ws_auth import verify_ws_token

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ROLES = {"frontend", "browser"}


def _resp(code: int, msg: str) -> dict:
    return {"statusCode": code, "body": msg}


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    authz = headers.get("authorization")
    qs = event.get("queryStringParameters") or {}
    session_qs = qs.get("session")
    role_qs = qs.get("role")

    if not authz or not authz.lower().startswith("bearer "):
        return _resp(401, "unauthorized")
    if not session_qs:
        return _resp(400, "missing session query param")
    if role_qs not in _ROLES:
        return _resp(400, "invalid role query param")

    token = authz[7:].strip()
    try:
        claims = verify_ws_token(token, expected_role=role_qs)
    except ValueError as e:
        logger.warning("token verification failed: %s", e)
        return _resp(401, "unauthorized")

    if claims["session"] != session_qs:
        return _resp(403, "forbidden")

    session = browser_sessions.get_session(session_qs)
    if not session:
        return _resp(404, "session not found")
    if session.get("user_id") != claims["sub"]:
        return _resp(403, "forbidden")

    connection_id = event["requestContext"]["connectionId"]
    browser_sessions.set_connection_id(session_qs, role=role_qs, connection_id=connection_id)
    logger.info("WS connect: session=%s role=%s conn=%s", session_qs, role_qs, connection_id)
    return _resp(200, "connected")
