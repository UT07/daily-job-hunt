import logging

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy SSM client — boto3.client at module load forces AWS_DEFAULT_REGION
# on every importer (including unit tests + runtime-import smoke). Same
# pattern as ai_helper.py shipped in PR #23.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


# Process-level caches — same rationale and shape as ai_helper.get_param /
# ai_helper.get_supabase: both used to redo their full work on EVERY call, a
# live SSM GetParameter round trip with KMS decryption, plus a fresh
# create_client() on top of two of those. Memoized in place rather than by
# importing ai_helper's copies: that would resolve here (same CodeUri) but is a
# wider refactor than this change, and the scrapers cannot do it at all.
# reset_caches() below is the escape hatch for a rotated parameter or a
# per-call test.
_param_cache: dict[str, str] = {}
_supabase = None


def get_param(name):
    # `not in` rather than a falsy check: a parameter that is legitimately the
    # empty string must stay cached, not be re-fetched on every call forever.
    if name not in _param_cache:
        _param_cache[name] = _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return _param_cache[name]


def get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client
        _supabase = create_client(
            get_param("/naukribaba/SUPABASE_URL"),
            get_param("/naukribaba/SUPABASE_SERVICE_KEY"),
        )
    return _supabase


def reset_caches():
    """Drop the memoized SSM parameters and Supabase client.

    Mirrors ai_helper.reset_caches(). Leaves the boto3 client alone — that one
    is a connection holder, not a cached value.
    """
    global _supabase
    _param_cache.clear()
    _supabase = None


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
