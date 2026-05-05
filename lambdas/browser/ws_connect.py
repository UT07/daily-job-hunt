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


def _resp(code: int, msg: str, extra_headers: dict | None = None) -> dict:
    resp: dict = {"statusCode": code, "body": msg}
    if extra_headers:
        resp["headers"] = extra_headers
    return resp


def _extract_token(headers: dict) -> tuple[str | None, str | None]:
    """Return (token, chosen_subprotocol) from either Authorization header
    OR the Sec-WebSocket-Protocol subprotocol header.

    Browser WebSocket clients can't set Authorization, so they pass the token
    as a subprotocol. Lambda must accept either form.

    Assumes caller has already lowercased all header keys.
    """
    auth = headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        # RFC 7235 §2.1: scheme name is case-insensitive. Strip the 7-char
        # "Bearer " prefix using fixed length, then strip residual whitespace
        # to match the pre-A2 behaviour.
        return auth[len("Bearer "):].strip(), None  # token, chosen_subprotocol

    proto = headers.get("sec-websocket-protocol")
    if proto:
        # Format: 'naukribaba-auth.<token>'. Optional comma-separated list.
        for entry in proto.split(","):
            entry = entry.strip()
            if entry.startswith("naukribaba-auth."):
                return entry[len("naukribaba-auth."):], "naukribaba-auth"
    return None, None


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    qs = event.get("queryStringParameters") or {}
    session_qs = qs.get("session")
    role_qs = qs.get("role")

    token, chosen_subprotocol = _extract_token(headers)
    if not token:
        return _resp(401, "unauthorized")
    if not session_qs:
        return _resp(400, "missing session query param")
    if role_qs not in _ROLES:
        return _resp(400, "invalid role query param")

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
    # Echo chosen subprotocol back per RFC 6455 when subprotocol auth was used
    extra = {"Sec-WebSocket-Protocol": chosen_subprotocol} if chosen_subprotocol else None
    return _resp(200, "connected", extra_headers=extra)
