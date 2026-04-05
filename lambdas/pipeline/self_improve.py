import json
import logging
import os
from datetime import datetime, timedelta

import boto3

from ai_helper import ai_complete, get_supabase
from self_improver import (
    generate_adjustments,
    should_revert_or_extend,
    execute_revert,
    save_pipeline_run,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _build_scraper_stats(metrics_data):
    """Build scraper_stats dict from raw pipeline_metrics rows."""
    by_scraper = {}
    for m in metrics_data:
        name = m["scraper_name"]
        by_scraper.setdefault(name, []).append(m)
    stats = {}
    for name, rows in by_scraper.items():
        sorted_rows = sorted(rows, key=lambda r: r["run_date"])
        stats[name] = {"yields": [r["jobs_found"] for r in sorted_rows]}
    return stats


def _build_score_stats(recent_jobs_data):
    """Build score_stats dict from recent job rows."""
    scores = [j["match_score"] for j in recent_jobs_data if j.get("match_score") is not None]
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    below_50 = sum(1 for s in scores if s < 50)
    return {
        "avg_score": avg,
        "pct_below_50": below_50 / len(scores) if scores else 0,
        "total": len(scores),
    }


def handler(event, context):
    user_id = event["user_id"]
    run_id = event.get("pipeline_run_id", "")
    run_data = event.get("run_data", {})

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

    # --- Generate new tiered adjustments ---
    new_adjustments = []
    if metrics.data:
        scraper_stats = _build_scraper_stats(metrics.data)
        score_stats = _build_score_stats(recent_jobs.data or [])
        new_adjustments = generate_adjustments(
            scraper_stats=scraper_stats,
            score_stats=score_stats,
        )

        # Write new adjustments to Supabase
        for adj in new_adjustments:
            adj["user_id"] = user_id
            adj["run_id"] = run_id
            adj["applied_at"] = datetime.utcnow().isoformat()
            db.table("pipeline_adjustments").insert(adj).execute()

        if new_adjustments:
            logger.info(f"[self_improve] Generated {len(new_adjustments)} new adjustments")

    # --- Check existing active adjustments for revert/confirm ---
    reverted = []
    confirmed = []

    active = db.table("pipeline_adjustments").select("*").in_(
        "status", ["auto_applied"]
    ).eq("user_id", user_id).execute().data or []

    for adj in active:
        runs_since = db.table("pipeline_runs").select("avg_base_score").eq(
            "user_id", user_id
        ).gte("started_at", adj.get("applied_at", "")).order("started_at").execute().data or []

        decision = should_revert_or_extend(adj, runs_since)
        if decision == "revert":
            execute_revert(db, adj)
            reverted.append(adj.get("id"))
            logger.info(f"[self_improve] Reverted adjustment {adj.get('id')}")
        elif decision == "confirm":
            db.table("pipeline_adjustments").update(
                {"status": "confirmed"}
            ).eq("id", adj["id"]).execute()
            confirmed.append(adj.get("id"))
            logger.info(f"[self_improve] Confirmed adjustment {adj.get('id')}")
        # "wait" and "extend" => no action, check again next run

    # --- AI analysis for self-improvement (legacy config path) ---
    if metrics.data and recent_jobs.data:
        recent_summary = json.dumps([
            {"title": j["title"], "score": j["match_score"], "source": j["source"]}
            for j in recent_jobs.data[:20]
        ])
        prompt = (
            "Analyze job search performance and suggest adjustments.\n\n"
            f"Metrics (last 30 days): {json.dumps(metrics.data[:20])}\n"
            f"Recent jobs (7 days): {recent_summary}\n\n"
            "Return JSON with adjustments:\n"
            '- query_weights: dict of query -> weight (0.0-1.0)\n'
            '- scraper_weights: dict of scraper -> weight (0.0-1.0)\n'
            '- scoring_threshold: {"threshold": int}\n'
            '- keyword_emphasis: {"keywords": [list]}\n'
        )
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

            logger.info(f"[self_improve] Saved {len(adjustments)} AI config adjustments")
        except Exception as e:
            logger.error(f"[self_improve] AI analysis failed: {e}")

    # --- Save pipeline run metrics ---
    if run_data:
        try:
            save_pipeline_run(db, user_id, run_data)
            logger.info("[self_improve] Saved pipeline run metrics")
        except Exception as e:
            logger.error(f"[self_improve] Failed to save pipeline run: {e}")

    return {
        "unhealthy_scrapers": unhealthy,
        "analyzed": bool(metrics.data),
        "new_adjustments": len(new_adjustments),
        "reverted": reverted,
        "confirmed": confirmed,
    }
