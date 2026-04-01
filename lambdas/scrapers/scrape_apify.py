"""Generic Apify scraper Lambda. Called with actor_id and run_input as params."""
import logging
import os
from datetime import datetime, timedelta

import boto3
import httpx
from apify_client import ApifyClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

BUDGET_ALERT_THRESHOLD_USD = float(os.environ.get("APIFY_BUDGET_ALERT_USD", "4.0"))
BUDGET_HARD_LIMIT_USD = float(os.environ.get("APIFY_BUDGET_LIMIT_USD", "4.80"))

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def _check_apify_budget(apify_key):
    """Check real Apify usage via API. Returns (usage_usd, limit_usd) or None on error."""
    try:
        resp = httpx.get(
            "https://api.apify.com/v2/users/me/limits",
            params={"token": apify_key},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            usage = data.get("current", {}).get("monthlyUsageUsd", 0)
            limit = data.get("limits", {}).get("maxMonthlyUsageUsd", 5)
            return usage, limit
    except Exception as e:
        logger.warning(f"Apify budget check failed: {e}")
    return None

def _send_budget_alert(usage_usd, limit_usd):
    """Send email alert when Apify budget is getting low."""
    try:
        gmail_user = get_param("/naukribaba/GMAIL_USER")
        gmail_pass = get_param("/naukribaba/GMAIL_APP_PASSWORD")
        import smtplib
        from email.mime.text import MIMEText
        remaining = limit_usd - usage_usd
        msg = MIMEText(
            f"Apify usage: ${usage_usd:.2f} / ${limit_usd:.2f}\n"
            f"Remaining: ${remaining:.2f}\n\n"
            f"Alert threshold: ${BUDGET_ALERT_THRESHOLD_USD:.2f}\n"
            f"Hard limit: ${BUDGET_HARD_LIMIT_USD:.2f}\n\n"
            f"Action: Create a new Apify account and update the API key in AWS SSM:\n"
            f"  aws ssm put-parameter --name /naukribaba/APIFY_API_KEY --value NEW_KEY --type SecureString --region eu-west-1 --overwrite"
        )
        msg["Subject"] = f"[NaukriBaba] Apify budget alert: ${remaining:.2f} remaining"
        msg["From"] = gmail_user
        msg["To"] = gmail_user
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)
        logger.info(f"Budget alert email sent: ${usage_usd:.2f}/{limit_usd:.2f}")
    except Exception as e:
        logger.error(f"Failed to send budget alert: {e}")

def handler(event, context):
    actor_id = event["actor_id"]
    run_input = event["run_input"]
    source = event["source"]
    normalizer_name = event.get("normalizer", source)
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", source).eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        logger.info(f"[{source}] Cache hit: {cached.count} jobs from last {cache_ttl_hours}h")
        return {"count": cached.count, "source": source, "cached": True}

    # Check real Apify budget before running actor
    apify_key = get_param("/naukribaba/APIFY_API_KEY")
    budget_info = _check_apify_budget(apify_key)
    if budget_info:
        usage_usd, limit_usd = budget_info
        remaining = limit_usd - usage_usd
        logger.info(f"[{source}] Apify budget: ${usage_usd:.2f}/${limit_usd:.2f} (${remaining:.2f} remaining)")

        if usage_usd >= BUDGET_HARD_LIMIT_USD:
            logger.warning(f"[{source}] Apify hard limit reached: ${usage_usd:.2f} >= ${BUDGET_HARD_LIMIT_USD:.2f}")
            _send_budget_alert(usage_usd, limit_usd)
            return {"count": 0, "source": source, "skipped": "budget_exceeded",
                    "apify_usage_usd": round(usage_usd, 2), "apify_remaining_usd": round(remaining, 2)}

        if usage_usd >= BUDGET_ALERT_THRESHOLD_USD:
            logger.warning(f"[{source}] Apify budget alert: ${usage_usd:.2f} >= ${BUDGET_ALERT_THRESHOLD_USD:.2f}")
            _send_budget_alert(usage_usd, limit_usd)

    try:
        # Run Apify actor
        client = ApifyClient(apify_key)
        logger.info(f"[{source}] Running actor {actor_id}")
        # max_items is required at call level for pay-per-result actors (e.g. Glassdoor)
        call_kwargs = {"run_input": run_input, "timeout_secs": 240}
        max_items = event.get("max_items")
        if max_items:
            call_kwargs["max_items"] = int(max_items)
        run = client.actor(actor_id).call(**call_kwargs)
        items = client.dataset(run["defaultDatasetId"]).list_items().items
        logger.info(f"[{source}] Got {len(items)} raw items")

        # Normalize
        from normalizers import (normalize_linkedin, normalize_indeed,
                                  normalize_glassdoor, normalize_generic_web)
        normalizer_map = {
            "linkedin": normalize_linkedin,
            "indeed": normalize_indeed,
            "glassdoor": normalize_glassdoor,
            "gradireland": normalize_generic_web,
            "irishjobs": normalize_generic_web,
            "jobsie": normalize_generic_web,
        }
        normalize_fn = normalizer_map.get(normalizer_name, normalize_generic_web)
        # Normalizers that need (items, source, query_hash) vs (items, query_hash)
        if normalizer_name in ("gradireland", "irishjobs", "jobsie"):
            jobs = normalize_fn(items, source, query_hash)
        else:
            jobs = normalize_fn(items, query_hash)

        # Validate schema
        valid_jobs = [j for j in jobs if j and j.get("title") and j.get("company")]
        if not valid_jobs:
            logger.warning(f"[{source}] 0 valid jobs after normalization")
            return {"count": 0, "source": source, "error": "no_valid_jobs"}

        # Deduplicate by job_hash within batch (Supabase upsert fails on intra-batch dupes)
        seen_hashes = set()
        unique_jobs = []
        for job in valid_jobs:
            if job["job_hash"] not in seen_hashes:
                seen_hashes.add(job["job_hash"])
                unique_jobs.append(job)
        valid_jobs = unique_jobs

        # Write to jobs_raw (bulk upsert)
        now = datetime.utcnow().isoformat()
        for job in valid_jobs:
            job["scraped_at"] = now
        db.table("jobs_raw").upsert(valid_jobs, on_conflict="job_hash").execute()

        # Estimate Apify cost (rough)
        cost_cents = max(1, len(items) // 20)
        logger.info(f"[{source}] Wrote {len(valid_jobs)} jobs, est cost: {cost_cents} cents")

        return {"count": len(valid_jobs), "source": source, "apify_cost_cents": cost_cents}
    except Exception as e:
        logger.error(f"[{source}] Actor call failed: {e}")
        return {"count": 0, "source": source, "error": str(e)}
