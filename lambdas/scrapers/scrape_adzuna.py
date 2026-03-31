"""Adzuna REST API scraper."""
import logging
import os
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "adzuna").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "adzuna", "cached": True}

    app_id = get_param("/naukribaba/ADZUNA_APP_ID")
    app_key = get_param("/naukribaba/ADZUNA_APP_KEY")

    from normalizers import normalize_adzuna
    all_jobs = []

    for query in queries:
        url = f"https://api.adzuna.com/v1/api/jobs/ie/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": query,
            "max_days_old": 3,
            "results_per_page": 50,
        }
        resp = httpx.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            jobs = normalize_adzuna(results, query_hash)
            all_jobs.extend(jobs)
            logger.info(f"[adzuna] Query '{query}': {len(jobs)} jobs")
        else:
            logger.warning(f"[adzuna] Query '{query}': HTTP {resp.status_code}")

    # Write to jobs_raw (bulk upsert)
    if all_jobs:
        now = datetime.utcnow().isoformat()
        for job in all_jobs:
            job["scraped_at"] = now
        db.table("jobs_raw").upsert(all_jobs, on_conflict="job_hash").execute()

    return {"count": len(all_jobs), "source": "adzuna"}
