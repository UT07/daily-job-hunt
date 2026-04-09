import json
import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timedelta

import boto3

from ai_helper import ai_complete, get_supabase
from self_improver import (
    generate_adjustments,
    analyze_query_effectiveness,
    analyze_keyword_gaps_for_resume,
    should_revert_or_extend,
    execute_revert,
    save_pipeline_run,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Keywords to look for when analysing high-scoring JDs
_TECH_KEYWORDS = {
    "kubernetes", "docker", "helm", "terraform", "ansible", "pulumi",
    "aws", "gcp", "azure",
    "python", "go", "golang", "java", "rust", "typescript", "javascript",
    "node", "bash", "sql",
    "react", "nextjs", "vue", "angular", "fastapi", "django", "flask",
    "spring boot", "express",
    "postgresql", "mysql", "mongodb", "redis", "dynamodb", "elasticsearch",
    "kafka", "rabbitmq", "spark", "airflow", "dbt", "snowflake",
    "machine learning", "llm", "langchain", "rag",
    "github actions", "ci/cd", "gitlab ci", "argocd",
    "prometheus", "grafana", "datadog", "opentelemetry",
    "microservices", "rest", "graphql", "grpc", "linux",
    "sre", "devops", "platform engineering",
}


def _build_scraper_stats(metrics_data: list[dict]) -> dict:
    """Build scraper_stats dict keyed by scraper_name from pipeline_metrics rows.

    Returns {scraper_name: {"yields": [jobs_found, ...], "matched": [jobs_matched, ...]}}
    """
    by_scraper: dict[str, list] = {}
    for m in metrics_data:
        name = m["scraper_name"]
        by_scraper.setdefault(name, []).append(m)

    stats = {}
    for name, rows in by_scraper.items():
        sorted_rows = sorted(rows, key=lambda r: r["run_date"])
        stats[name] = {
            "yields": [r["jobs_found"] for r in sorted_rows],
            "matched": [r.get("jobs_matched", 0) for r in sorted_rows],
        }
    return stats


def _build_score_stats(recent_jobs: list[dict]) -> dict | None:
    """Compute score distribution stats from recent job rows."""
    scores = [j["match_score"] for j in recent_jobs if j.get("match_score") is not None]
    if not scores:
        return None

    avg = sum(scores) / len(scores)
    below_50 = sum(1 for s in scores if s < 50)
    tier_counts = Counter(j.get("score_tier", "D") for j in recent_jobs)
    return {
        "avg_score": round(avg, 1),
        "pct_below_50": round(below_50 / len(scores), 3),
        "total": len(scores),
        "tier_distribution": dict(tier_counts),
    }


def _build_current_run_stats(db, user_id: str, today: str) -> dict:
    """Query Supabase for today's run metrics.

    Returns a dict suitable for passing to save_pipeline_run().
    """
    # Jobs scored today
    scored = db.table("jobs").select(
        "match_score, score_tier, resume_s3_url, description, title"
    ).eq("user_id", user_id).gte("scored_at", today).execute().data or []

    scores = [j["match_score"] for j in scored if j.get("match_score") is not None]
    avg_base = round(sum(scores) / len(scores), 1) if scores else None

    # Compilation success: jobs that have a tailored PDF uploaded today (resume_s3_url set)
    compile_ok = sum(1 for j in scored if j.get("resume_s3_url"))
    compile_fail = len(scored) - compile_ok

    # Jobs scraped today (from pipeline_metrics)
    pm = db.table("pipeline_metrics").select("jobs_found").eq(
        "user_id", user_id
    ).eq("run_date", today).execute().data or []
    jobs_scraped = sum(r.get("jobs_found", 0) for r in pm)

    return {
        "started_at": today,
        "jobs_scraped": jobs_scraped,
        "jobs_scored": len(scored),
        "jobs_matched": sum(1 for j in scored if (j.get("match_score") or 0) >= 60),
        "jobs_tailored": compile_ok,
        "avg_base_score": avg_base,
        "compile_ok": compile_ok,
        "compile_fail": compile_fail,
        "_scored_jobs": scored,  # carry forward for keyword analysis
    }


def _build_keyword_stats(scored_jobs: list[dict], min_score: float = 70.0) -> dict:
    """Count keyword frequency in high-scoring job descriptions.

    Returns {keyword: {"count": int, "avg_job_score": float}} only for keywords
    that appear in 3+ JDs above min_score.
    """
    high = [j for j in scored_jobs if (j.get("match_score") or 0) >= min_score]
    if not high:
        return {}

    freq: Counter = Counter()
    score_totals: Counter = Counter()
    for job in high:
        text = ((job.get("description") or "") + " " + (job.get("title") or "")).lower()
        for kw in _TECH_KEYWORDS:
            if kw in text:
                freq[kw] += 1
                score_totals[kw] += job.get("match_score", 0)

    return {
        kw: {
            "count": count,
            "avg_job_score": round(score_totals[kw] / count, 1),
        }
        for kw, count in freq.items()
        if count >= 3
    }


def _build_query_stats(db, user_id: str, thirty_days_ago: str) -> dict:
    """Build per-query match-rate history from pipeline_metrics.

    pipeline_metrics doesn't store per-query data today, so this builds
    approximate per-source match rates and wraps them in the query_stats shape
    that analyze_query_effectiveness() expects.
    """
    rows = db.table("pipeline_metrics").select(
        "scraper_name, jobs_found, jobs_matched, run_date"
    ).eq("user_id", user_id).gte("run_date", thirty_days_ago).execute().data or []

    by_source: dict[str, list] = {}
    for r in rows:
        name = r["scraper_name"]
        found = r.get("jobs_found", 0)
        matched = r.get("jobs_matched", 0)
        rate = round(matched / found, 3) if found > 0 else 0.0
        by_source.setdefault(name, []).append(rate)

    # Wrap in the shape analyze_query_effectiveness() expects:
    # {query_name: {"match_rates": [...]}}
    return {name: {"match_rates": rates} for name, rates in by_source.items()}


def _notify_medium_risk(user_id: str, adjustments: list[dict]):
    """Fire-and-forget Lambda invocation to alert the user about medium-risk adjustments."""
    notify_fn = os.environ.get("NOTIFY_ERROR_FUNCTION", "naukribaba-notify-error")
    medium = [a for a in adjustments if a.get("risk_level") == "medium" and a.get("notify")]
    if not medium:
        return
    try:
        lambda_client = boto3.client("lambda")
        summary = "; ".join(a["reason"] for a in medium[:3])
        lambda_client.invoke(
            FunctionName=notify_fn,
            InvocationType="Event",
            Payload=json.dumps({
                "user_id": user_id,
                "error": f"[Self-Improve] {len(medium)} medium-risk adjustments auto-applied: {summary}",
                "step": "self_improve_medium_risk",
            }).encode(),
        )
        logger.info(f"[self_improve] Notified user of {len(medium)} medium-risk adjustments")
    except Exception as e:
        logger.warning(f"[self_improve] Notification failed: {e}")


def handler(event, context):
    user_id = event["user_id"]
    run_id = str(uuid.uuid4())
    started_at = event.get("started_at", "")
    matched_count_from_event = event.get("matched_count", 0)

    db = get_supabase()

    today = datetime.utcnow().date().isoformat()
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # --- 1. Fetch historical pipeline_metrics (30 days for scraper health) ---
    metrics = db.table("pipeline_metrics").select("*").eq("user_id", user_id) \
        .gte("run_date", thirty_days_ago[:10]).execute()

    # --- 2. Fetch recent jobs (7 days) for score distribution ---
    recent_jobs = db.table("jobs").select(
        "source, match_score, score_tier, title, description"
    ).eq("user_id", user_id).gte("first_seen", seven_days_ago).execute()

    # --- 3. Scraper health check: 3+ consecutive days of zero results → notify ---
    DISABLED_SCRAPERS = {"glassdoor", "adzuna", "gradireland"}
    scraper_names = set(m["scraper_name"] for m in (metrics.data or []))
    unhealthy = []
    for scraper in scraper_names:
        if scraper in DISABLED_SCRAPERS:
            continue
        scraper_metrics = sorted(
            [m for m in metrics.data if m["scraper_name"] == scraper],
            key=lambda m: m["run_date"], reverse=True
        )[:3]
        if len(scraper_metrics) >= 3 and all(m["jobs_found"] == 0 for m in scraper_metrics):
            unhealthy.append(scraper)

    if unhealthy:
        logger.warning(f"[self_improve] Unhealthy scrapers (3+ days zero): {unhealthy}")
        notify_fn = os.environ.get("NOTIFY_ERROR_FUNCTION", "naukribaba-notify-error")
        lambda_client = boto3.client("lambda")
        try:
            lambda_client.invoke(
                FunctionName=notify_fn,
                InvocationType="Event",
                Payload=json.dumps({
                    "user_id": user_id,
                    "error": f"Scrapers with 3+ days of zero results: {', '.join(unhealthy)}",
                    "step": "scraper_health_check",
                }).encode(),
            )
        except Exception as e:
            logger.warning(f"[self_improve] Failed to send scraper health alert: {e}")

    # --- 4. Build analysis inputs ---
    scraper_stats = _build_scraper_stats(metrics.data or [])
    score_stats = _build_score_stats(recent_jobs.data or [])
    query_stats = _build_query_stats(db, user_id, thirty_days_ago[:10])

    # --- 5. Current run data from Supabase (not from Step Functions state) ---
    current_run = _build_current_run_stats(db, user_id, today)
    scored_jobs_today = current_run.pop("_scored_jobs", [])
    keyword_stats = _build_keyword_stats(scored_jobs_today)

    logger.info(
        f"[self_improve] Today: {current_run['jobs_scraped']} scraped, "
        f"{current_run['jobs_scored']} scored, "
        f"{current_run['jobs_tailored']} tailored "
        f"({current_run['compile_fail']} compile failures). "
        f"Avg score: {current_run['avg_base_score']}. "
        f"Top keywords: {list(keyword_stats.keys())[:5]}"
    )

    if score_stats:
        dist = score_stats.get("tier_distribution", {})
        logger.info(
            f"[self_improve] 7-day score stats: avg={score_stats['avg_score']}, "
            f"tiers={dist}, pct_below_50={score_stats['pct_below_50']:.0%}"
        )

    # --- 6. Quality stats from compile failure rate ---
    quality_stats = None
    if current_run["jobs_tailored"] + current_run["compile_fail"] > 0:
        total_attempted = current_run["jobs_tailored"] + current_run["compile_fail"]
        fail_rate = current_run["compile_fail"] / total_attempted
        # Use trend placeholder — we don't have historical quality scores yet
        quality_stats = {
            "compile_fail_rate": round(fail_rate, 3),
            "trend": "declining" if fail_rate > 0.2 else "stable",
            "avg_last_3": None,
            "avg_prev_3": None,
        }

    # --- 7. Generate tiered adjustments ---
    new_adjustments: list[dict] = []
    if metrics.data:
        new_adjustments = generate_adjustments(
            scraper_stats=scraper_stats,
            score_stats=score_stats,
            quality_stats=quality_stats,
            keyword_stats=keyword_stats if keyword_stats else None,
        )

    # Also check query effectiveness
    query_adj = analyze_query_effectiveness(query_stats, threshold=0.03, min_runs=3)
    new_adjustments.extend(query_adj)

    # Keyword gap suggestions for base resume (medium-risk, user reviews before applying)
    if keyword_stats:
        kw_adj = analyze_keyword_gaps_for_resume(keyword_stats, min_jobs=10)
        new_adjustments.extend(kw_adj)

    # Write new adjustments to Supabase
    # Strip transient keys that aren't DB columns before inserting
    _NON_DB_KEYS = {"notify"}
    for adj in new_adjustments:
        adj["user_id"] = user_id
        adj["run_id"] = run_id
        adj["applied_at"] = datetime.utcnow().isoformat()
        row = {k: v for k, v in adj.items() if k not in _NON_DB_KEYS}
        db.table("pipeline_adjustments").insert(row).execute()

    if new_adjustments:
        logger.info(f"[self_improve] Generated {len(new_adjustments)} new adjustments")

    # Notify about medium-risk adjustments that were auto-applied
    _notify_medium_risk(user_id, new_adjustments)

    # --- 8. Evaluate existing auto_applied adjustments: revert or confirm ---
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
            logger.info(f"[self_improve] Reverted adjustment {adj.get('id')}: {adj.get('reason','')[:80]}")
        elif decision == "confirm":
            db.table("pipeline_adjustments").update(
                {"status": "confirmed"}
            ).eq("id", adj["id"]).execute()
            confirmed.append(adj.get("id"))
            logger.info(f"[self_improve] Confirmed adjustment {adj.get('id')}: {adj.get('reason','')[:80]}")
        # "wait" and "extend" → no action this run

    # --- 9. AI freeform analysis for self_improvement_config (legacy query/scraper weights) ---
    if metrics.data and recent_jobs.data:
        recent_summary = json.dumps([
            {"title": j["title"], "score": j["match_score"], "source": j["source"]}
            for j in (recent_jobs.data or [])[:20]
        ])
        prompt = (
            "Analyze job search performance and suggest adjustments.\n\n"
            f"Metrics (last 30 days): {json.dumps((metrics.data or [])[:20])}\n"
            f"Recent jobs (7 days): {recent_summary}\n\n"
            "Return JSON with adjustments:\n"
            '- query_weights: dict of query -> weight (0.0-1.0)\n'
            '- scraper_weights: dict of scraper -> weight (0.0-1.0)\n'
            '- scoring_threshold: {"threshold": int}\n'
            '- keyword_emphasis: {"keywords": [list]}\n'
        )
        try:
            result = ai_complete(
                prompt,
                system="You are a job search optimization AI. Return only valid JSON.",
            )
            adjustments_ai = json.loads(result["content"])

            for config_type, config_data in adjustments_ai.items():
                db.table("self_improvement_config").upsert({
                    "user_id": user_id,
                    "config_type": config_type,
                    "config_data": config_data,
                    "applied_at": datetime.utcnow().isoformat(),
                }, on_conflict="user_id,config_type").execute()

            logger.info(f"[self_improve] Saved {len(adjustments_ai)} AI config entries")
        except Exception as e:
            logger.error(f"[self_improve] AI analysis failed: {e}")

    # --- 10. Save pipeline_runs row ---
    run_data = {
        "started_at": started_at or today,
        "jobs_scraped": current_run["jobs_scraped"],
        "jobs_new": current_run["jobs_scored"],  # approximation when no dedup count available
        "jobs_scored": current_run["jobs_scored"],
        "jobs_matched": current_run["jobs_matched"],
        "jobs_tailored": current_run["jobs_tailored"],
        "avg_base_score": current_run["avg_base_score"],
        "scraper_stats": {
            name: {"yields": stats["yields"], "matched": stats.get("matched", [])}
            for name, stats in scraper_stats.items()
        },
        "active_adjustments": [a.get("id") for a in active],
    }
    try:
        save_pipeline_run(db, user_id, run_data)
        logger.info("[self_improve] Saved pipeline_runs row")
    except Exception as e:
        logger.error(f"[self_improve] Failed to save pipeline run: {e}")

    return {
        "unhealthy_scrapers": unhealthy,
        "analyzed": bool(metrics.data),
        "new_adjustments": len(new_adjustments),
        "reverted": reverted,
        "confirmed": confirmed,
        "score_stats": score_stats,
        "top_keywords": list(keyword_stats.keys())[:10] if keyword_stats else [],
    }
