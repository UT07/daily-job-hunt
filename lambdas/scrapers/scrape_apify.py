"""Generic Apify scraper Lambda. Called with actor_id and run_input as params."""
import json
import logging
import os
from datetime import datetime, timedelta

import boto3
from apify_client import ApifyClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def handler(event, context):
    actor_id = event["actor_id"]
    run_input = event["run_input"]
    source = event["source"]
    normalizer_name = event.get("normalizer", source)
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", source).eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        logger.info(f"[{source}] Cache hit: {cached.count} jobs from last {cache_ttl_hours}h")
        return {"count": cached.count, "source": source, "cached": True}

    # Check Apify budget
    first_of_month = datetime.utcnow().replace(day=1).date().isoformat()
    monthly = db.table("pipeline_metrics").select("apify_cost_cents") \
        .gte("created_at", first_of_month).execute()
    total_cents = sum(r.get("apify_cost_cents", 0) for r in (monthly.data or []))
    budget = int(os.environ.get("APIFY_MONTHLY_BUDGET_CENTS", "500"))
    if total_cents >= budget:
        logger.warning(f"[{source}] Apify budget exceeded: {total_cents}/{budget} cents")
        return {"count": 0, "source": source, "skipped": "budget_exceeded"}

    # Run Apify actor
    apify_key = get_param("/naukribaba/APIFY_API_KEY")
    client = ApifyClient(apify_key)
    logger.info(f"[{source}] Running actor {actor_id}")
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=240)
    items = client.dataset(run["defaultDatasetId"]).list_items().items
    logger.info(f"[{source}] Got {len(items)} raw items")

    # Normalize
    from normalizers import (normalize_linkedin, normalize_indeed,
                              normalize_generic_web)
    normalizer_map = {
        "linkedin": normalize_linkedin,
        "indeed": normalize_indeed,
        "glassdoor": normalize_generic_web,
        "gradireland": normalize_generic_web,
        "irishjobs": normalize_generic_web,
        "jobsie": normalize_generic_web,
    }
    normalize_fn = normalizer_map.get(normalizer_name, normalize_generic_web)
    if normalizer_name in ("glassdoor", "gradireland", "irishjobs", "jobsie"):
        jobs = normalize_fn(items, source, query_hash)
    else:
        jobs = normalize_fn(items, query_hash)

    # Validate schema
    valid_jobs = [j for j in jobs if j and j.get("title") and j.get("company")]
    if not valid_jobs:
        logger.warning(f"[{source}] 0 valid jobs after normalization")
        return {"count": 0, "source": source, "error": "no_valid_jobs"}

    # Write to jobs_raw (upsert)
    for job in valid_jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    # Estimate Apify cost (rough)
    cost_cents = max(1, len(items) // 20)
    logger.info(f"[{source}] Wrote {len(valid_jobs)} jobs, est cost: {cost_cents} cents")

    return {"count": len(valid_jobs), "source": source, "apify_cost_cents": cost_cents}
