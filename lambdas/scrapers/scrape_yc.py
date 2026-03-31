"""YC Jobs scraper — fetches from WorkAtAStartup."""
import logging
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
    cache_ttl_hours = event.get("cache_ttl_hours", 48)

    db = get_supabase()

    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", "yc").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "yc", "cached": True}

    headers = {
        "X-Inertia": "true",
        "X-Inertia-Version": "",
        "Accept": "text/html, application/xhtml+xml",
    }

    from normalizers import normalize_generic_web
    all_jobs = []

    for query in queries:
        url = f"https://www.workatastartup.com/companies?query={query}"
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            try:
                data = resp.json()
                companies = data.get("props", {}).get("companies", [])
                for co in companies:
                    for job in co.get("jobs", []):
                        all_jobs.append({
                            "title": job.get("title", ""),
                            "company": co.get("name", ""),
                            "description": job.get("description", ""),
                            "location": job.get("location", ""),
                            "apply_url": f"https://www.workatastartup.com/jobs/{job.get('id', '')}",
                        })
            except Exception as e:
                logger.warning(f"[yc] Parse error: {e}")

    jobs = normalize_generic_web(all_jobs, "yc", query_hash)
    for job in jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    logger.info(f"[yc] {len(jobs)} jobs")
    return {"count": len(jobs), "source": "yc"}
