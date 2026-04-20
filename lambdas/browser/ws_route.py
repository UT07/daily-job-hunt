"""WebSocket $default handler — routes messages between frontend and Fargate."""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stub: logs incoming message.
    TODO (Plan 3): Parse action from body, route to appropriate handler
    (click, type, scroll, screenshot_ack), relay between frontend and Fargate connections.
    """
    connection_id = event.get("requestContext", {}).get("connectionId", "unknown")
    body = event.get("body", "{}")
    logger.info(f"WebSocket message from {connection_id}: {body[:200]}")
    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
