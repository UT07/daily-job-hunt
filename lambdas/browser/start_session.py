"""Start browser session — launches Fargate task with Chrome."""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Stub: returns 501 Not Implemented.
    TODO (Plan 3): Validate auth, check profile completeness, launch Fargate task,
    create DynamoDB session, return session_id + WebSocket URL.
    """
    logger.info(f"start-session called: {json.dumps(event.get('body', '{}'))[:200]}")
    return {
        "statusCode": 501,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "error": "Not implemented",
            "message": "Browser session management coming in Plan 3"
        })
    }
