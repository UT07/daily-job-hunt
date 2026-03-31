import logging

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
    db = get_supabase()

    # Get active jobs with apply_urls
    jobs = db.table("jobs").select("job_id, apply_url, job_hash") \
        .eq("is_expired", False) \
        .not_.is_("apply_url", "null") \
        .limit(100).execute()

    expired_count = 0
    for job in (jobs.data or []):
        url = job.get("apply_url", "")
        if not url:
            continue
        try:
            resp = httpx.head(url, timeout=10, follow_redirects=True)
            if resp.status_code in (404, 410):
                db.table("jobs").update({"is_expired": True}).eq("job_id", job["job_id"]).execute()
                expired_count += 1
        except Exception:
            pass  # Network errors don't mean expired

    logger.info(f"[check_expiry] Checked {len(jobs.data or [])} jobs, {expired_count} expired")
    return {"checked": len(jobs.data or []), "expired": expired_count}
