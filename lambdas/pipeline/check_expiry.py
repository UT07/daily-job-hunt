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


def get_param(name):
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


# PostgREST caps any single request at its configured max-rows (1,000 by
# default here) regardless of what `.limit()` asks for — this project has
# been bitten by that silent cap before. A page size well under that,
# walked with `.range()` until a short page signals the end, is the only
# way to see the whole table. Keep this well below 1,000 and below any
# repointed instance's max-rows.
_PAGE_SIZE = 500


def _fetch_active_jobs_with_apply_url(db, page_size: int = None) -> list[dict]:
    """Fetch every active job with an apply_url, walking the full table.

    Reads ALL pages before any caller mutates rows. Filtering on
    `is_expired=False` while also updating that same flag mid-scan would
    shift what each subsequent `.range()` offset means (a row flipped to
    expired on page N shifts every later row up by one, so an
    offset-based page N+1 request silently skips whatever just shifted
    into page N's tail) — so the fetch phase is fully separated from the
    update phase in `handler()` below.

    `.order()` on the composite (job_id, user_id) key (jobs' primary key)
    gives a fully deterministic row order; without a unique tiebreaker,
    `.range()` pagination over ties is not guaranteed stable.

    ``page_size`` defaults to the module-level ``_PAGE_SIZE`` looked up at
    call time (not bound as a mutable default argument) so tests can
    `monkeypatch.setattr(check_expiry, "_PAGE_SIZE", ...)` to exercise the
    pagination loop with a handful of fixture rows instead of hundreds.
    """
    if page_size is None:
        page_size = _PAGE_SIZE
    jobs: list[dict] = []
    offset = 0
    while True:
        page = (
            db.table("jobs")
            .select("job_id, user_id, apply_url, job_hash")
            .eq("is_expired", False)
            .not_.is_("apply_url", "null")
            .order("job_id")
            .order("user_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = page.data or []
        jobs.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return jobs


def handler(event, context):
    db = get_supabase()

    # Get active jobs with apply_urls (paginated — see _fetch_active_jobs_with_apply_url)
    jobs_data = _fetch_active_jobs_with_apply_url(db)

    expired_count = 0
    for job in jobs_data:
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

    logger.info(f"[check_expiry] Checked {len(jobs_data)} jobs, {expired_count} expired")
    return {"checked": len(jobs_data), "expired": expired_count}
