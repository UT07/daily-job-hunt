"""WebSocket $disconnect handler — cleans up DynamoDB session."""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stub: logs disconnection.
    TODO (Plan 3): Update DynamoDB session, trigger Fargate cleanup if both sides disconnected.
    """
    connection_id = event.get("requestContext", {}).get("connectionId", "unknown")
    logger.info(f"WebSocket disconnect: {connection_id}")
    return {"statusCode": 200, "body": "Disconnected"}
