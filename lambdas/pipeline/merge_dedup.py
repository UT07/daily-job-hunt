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
    db = get_supabase()
    today = datetime.utcnow().date().isoformat()

    # Get today's scraped jobs
    result = db.table("jobs_raw").select("job_hash, title, company, source, description") \
        .gte("scraped_at", today).execute()

    if not result.data:
        return {"new_job_hashes": [], "total_new": 0}

    # Cross-source dedup: keep richest version (longest description)
    seen = {}
    for job in result.data:
        key = f"{job['company'].lower().strip()}|{job['title'].lower().strip()}"
        existing = seen.get(key)
        if not existing or len(job.get("description") or "") > len(existing.get("description") or ""):
            seen[key] = job

    # Check which are truly new (not already in jobs table for this user)
    user_id = event.get("user_id", "")
    existing_hashes = set()
    if user_id:
        existing = db.table("jobs").select("job_hash").eq("user_id", user_id) \
            .not_.is_("job_hash", "null").execute()
        existing_hashes = {j["job_hash"] for j in (existing.data or [])}

    new_hashes = [j["job_hash"] for j in seen.values() if j["job_hash"] not in existing_hashes]

    logger.info(f"[merge_dedup] {len(result.data)} scraped → {len(seen)} unique → {len(new_hashes)} new")
    return {"new_job_hashes": new_hashes, "total_new": len(new_hashes)}
