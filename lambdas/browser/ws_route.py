"""WebSocket $default handler — relays text messages between frontend and browser.

Screenshots bypass this Lambda (design §7.3): Fargate posts them directly
to the frontend's connection via the Management API. Only text/JSON control
messages (≤128KB) flow through here."""

from __future__ import annotations

import logging
import os

from botocore.exceptions import ClientError

import shared.browser_sessions as browser_sessions

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WEBSOCKET_API_ID = os.environ.get("WEBSOCKET_API_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

_MAX_BODY = 128 * 1024


def handler(event, context):
    body = event.get("body", "") or ""
    if len(body) > _MAX_BODY:
        return {"statusCode": 413, "body": "payload too large"}

    connection_id = event.get("requestContext", {}).get("connectionId", "")
    found = browser_sessions.find_session_by_connection(connection_id)
    if not found:
        return {"statusCode": 200, "body": "noop"}

    session, sender_role = found
    peer_role = "browser" if sender_role == "frontend" else "frontend"
    peer_conn = session.get(f"ws_connection_{peer_role}")
    if not peer_conn:
        return {"statusCode": 200, "body": "no peer"}

    try:
        browser_sessions.post_to_connection(
            api_id=WEBSOCKET_API_ID,
            region=AWS_REGION,
            connection_id=peer_conn,
            data=body.encode("utf-8"),
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "GoneException":
            browser_sessions.clear_connection_id(session["session_id"], role=peer_role)
        else:
            logger.exception("PostToConnection failed: %s", e)
    return {"statusCode": 200, "body": "ok"}
