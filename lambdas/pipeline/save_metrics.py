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
    score_result = event.get("score_result", {})
    dedup_result = event.get("dedup_result", {})

    db = get_supabase()
    now = datetime.utcnow()
    today = now.date().isoformat()

    # Save per-scraper metrics
    for result in scraper_results:
        db.table("pipeline_metrics").insert({
            "user_id": user_id,
            "run_date": today,
            "scraper_name": result.get("source", "unknown"),
            "jobs_found": result.get("count", 0),
            "apify_cost_cents": result.get("apify_cost_cents", 0),
            "error_message": result.get("error"),
        }).execute()

    # Save summary run record to `runs` table so dashboard shows latest status
    total_found = sum(r.get("count", 0) for r in scraper_results if not r.get("skipped"))
    total_new = dedup_result.get("total_new", 0) if isinstance(dedup_result, dict) else 0
    matched_count = score_result.get("matched_count", 0) if isinstance(score_result, dict) else 0

    import uuid
    db.table("runs").upsert({
        "run_id": str(uuid.uuid4()),
        "user_id": user_id,
        "run_date": today,
        "run_time": now.strftime("%H:%M:%S"),
        "raw_jobs": total_found,
        "unique_jobs": total_new,
        "matched_jobs": matched_count,
        "resumes_generated": 0,  # updated by post-processing
        "status": "completed",
        "completed_at": now.isoformat(),
    }, on_conflict="user_id,run_date").execute()

    logger.info(f"[save_metrics] Saved {len(scraper_results)} scraper metrics + run summary "
                f"(found={total_found}, new={total_new}, matched={matched_count})")
    return {"saved": len(scraper_results)}
