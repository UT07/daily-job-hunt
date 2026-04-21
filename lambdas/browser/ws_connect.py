"""WebSocket $connect handler — validates JWT, creates DynamoDB session."""

import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stub: returns 200 to accept WebSocket connection.
    TODO (Plan 3): Validate JWT from query string, create DynamoDB session row.
    """
    connection_id = event.get("requestContext", {}).get("connectionId", "unknown")
    logger.info(f"WebSocket connect: {connection_id}")
    return {"statusCode": 200, "body": "Connected"}
