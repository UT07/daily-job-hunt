"""Stop browser session — saves Chrome profile to S3, stops Fargate task."""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stub: returns 501 Not Implemented.
    TODO (Plan 3): Load session from DynamoDB, send shutdown signal to Fargate,
    wait for profile save to S3, update session status, stop task.
    """
    logger.info(f"stop-session called: {json.dumps(event.get('body', '{}'))[:200]}")
    return {
        "statusCode": 501,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "error": "Not implemented",
            "message": "Browser session management coming in Plan 3"
        })
    }
