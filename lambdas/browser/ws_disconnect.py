"""WebSocket $disconnect handler.

Clears the matching slot on the session row; if both sides are now absent,
transitions status=ended and best-effort stops the Fargate task."""

from __future__ import annotations

import logging
import os

import boto3

import shared.browser_sessions as browser_sessions

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "")


def handler(event, context):
    connection_id = event.get("requestContext", {}).get("connectionId", "")
    try:
        found = browser_sessions.find_session_by_connection(connection_id)
        if not found:
            return {"statusCode": 200, "body": "noop"}

        session, role = found
        session_id = session["session_id"]
        browser_sessions.clear_connection_id(session_id, role=role)

        other_role = "browser" if role == "frontend" else "frontend"
        other_conn = session.get(f"ws_connection_{other_role}")
        if not other_conn:
            browser_sessions.update_status(session_id, "ended")
            task_arn = session.get("fargate_task_arn")
            if task_arn and CLUSTER_ARN:
                try:
                    boto3.client("ecs").stop_task(
                        cluster=CLUSTER_ARN,
                        task=task_arn,
                        reason="Both WS peers disconnected",
                    )
                except Exception as e:
                    logger.warning("stop_task failed for %s: %s", task_arn, e)
        logger.info("WS disconnect: session=%s role=%s peer_left=%s",
                    session_id, role, not other_conn)
    except Exception as e:
        logger.exception("WS disconnect handler failed: %s", e)
    return {"statusCode": 200, "body": "disconnected"}
