import logging
from datetime import datetime

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def handler(event, context):
    user_id = event["user_id"]
    scraper_results = event.get("scraper_results", [])
    execution_id = event.get("execution_id", "")

    db = get_supabase()
    today = datetime.utcnow().date().isoformat()

    for result in scraper_results:
        db.table("pipeline_metrics").insert({
            "user_id": user_id,
            "run_date": today,
            "execution_id": execution_id,
            "scraper_name": result.get("source", "unknown"),
            "jobs_found": result.get("count", 0),
            "apify_cost_cents": result.get("apify_cost_cents", 0),
            "error_message": result.get("error"),
        }).execute()

    logger.info(f"[save_metrics] Saved {len(scraper_results)} scraper metrics")
    return {"saved": len(scraper_results)}
