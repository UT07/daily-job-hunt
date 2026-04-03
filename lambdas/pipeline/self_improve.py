import json
import logging
import os
from datetime import datetime, timedelta

import boto3

from ai_helper import ai_complete, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    user_id = event["user_id"]

    db = get_supabase()

    # Read historical data (30 days)
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    metrics = db.table("pipeline_metrics").select("*").eq("user_id", user_id) \
        .gte("run_date", thirty_days_ago[:10]).execute()

    recent_jobs = db.table("jobs").select("source, match_score, title").eq("user_id", user_id) \
        .gte("first_seen", seven_days_ago).execute()

    # Check scraper health: 3+ consecutive days of zero results
    scraper_names = set(m["scraper_name"] for m in (metrics.data or []))
    unhealthy = []
    for scraper in scraper_names:
        scraper_metrics = sorted(
            [m for m in metrics.data if m["scraper_name"] == scraper],
            key=lambda m: m["run_date"], reverse=True
        )[:3]
        if len(scraper_metrics) >= 3 and all(m["jobs_found"] == 0 for m in scraper_metrics):
            unhealthy.append(scraper)

    if unhealthy:
        logger.warning(f"[self_improve] Unhealthy scrapers: {unhealthy}")
        notify_fn = os.environ.get("NOTIFY_ERROR_FUNCTION", "naukribaba-notify-error")
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=notify_fn,
            InvocationType="Event",
            Payload=json.dumps({
                "user_id": user_id,
                "error": f"Scrapers with 3+ days of zero results: {', '.join(unhealthy)}",
                "step": "scraper_health_check",
            }).encode(),
        )

    # AI analysis for self-improvement
    if metrics.data and recent_jobs.data:
        prompt = f"""Analyze job search performance and suggest adjustments.

Metrics (last 30 days): {json.dumps(metrics.data[:20])}
Recent jobs (7 days): {json.dumps([{{"title": j["title"], "score": j["match_score"], "source": j["source"]}} for j in recent_jobs.data[:20]])}

Return JSON with adjustments:
- query_weights: dict of query -> weight (0.0-1.0)
- scraper_weights: dict of scraper -> weight (0.0-1.0)
- scoring_threshold: {{"threshold": int}}
- keyword_emphasis: {{"keywords": [list]}}
"""
        try:
            result = ai_complete(prompt, system="You are a job search optimization AI. Return only valid JSON.")
            adjustments = json.loads(result["content"])

            for config_type, config_data in adjustments.items():
                db.table("self_improvement_config").upsert({
                    "user_id": user_id,
                    "config_type": config_type,
                    "config_data": config_data,
                    "applied_at": datetime.utcnow().isoformat(),
                }, on_conflict="user_id,config_type").execute()

            logger.info(f"[self_improve] Saved {len(adjustments)} adjustments")
        except Exception as e:
            logger.error(f"[self_improve] AI analysis failed: {e}")

    return {"unhealthy_scrapers": unhealthy, "analyzed": bool(metrics.data)}
