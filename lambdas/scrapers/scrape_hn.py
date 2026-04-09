"""HN Hiring scraper — fetches latest 'Who is hiring?' thread via Algolia API.

Uses search_by_date (not search) so we always get the most recent monthly
thread, and paginates comments to capture the full thread.
"""
import logging
import re
import time
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Matches "Ask HN: Who is hiring? (Month Year)"
_THREAD_TITLE_RE = re.compile(
    r"^Ask HN: Who is hiring\?", re.IGNORECASE
)

# Max comments per page from Algolia (their limit)
_COMMENTS_PAGE_SIZE = 200
# Max pages to paginate (safety cap: 5 * 200 = 1000 comments)
_MAX_COMMENT_PAGES = 5


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def parse_hn_comment(text: str) -> dict:
    """Parse a HN hiring comment into job fields."""
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r'<[^>]+>', '\n', text).strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if not lines:
        return None

    first_line = lines[0]
    parts = [p.strip() for p in first_line.split('|')]
    company = parts[0] if parts else ""
    title = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""

    description = '\n'.join(lines)

    if not company or not title:
        return None

    return {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
    }


def _find_latest_thread() -> str | None:
    """Find the objectID of the latest 'Who is hiring?' monthly thread.

    Uses search_by_date (date-sorted) with a 35-day lookback so we always
    get the current month's thread rather than a years-old relevance hit.
    Returns None if no matching thread is found.
    """
    search_url = "https://hn.algolia.com/api/v1/search_by_date"
    cutoff = int(time.time()) - 35 * 86400  # 35 days ago
    params = {
        "query": '"Who is hiring"',
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff}",
        "hitsPerPage": 10,
    }
    resp = httpx.get(search_url, params=params, timeout=15)
    if resp.status_code != 200:
        logger.warning(f"[hn_hiring] Thread search: HTTP {resp.status_code}")
        return None

    hits = resp.json().get("hits", [])
    for hit in hits:
        title = hit.get("title", "")
        if _THREAD_TITLE_RE.search(title):
            logger.info(f"[hn_hiring] Found thread: {title} (id={hit['objectID']})")
            return hit["objectID"]

    logger.warning(f"[hn_hiring] No matching thread in {len(hits)} hits")
    return None


def _fetch_all_comments(thread_id: str) -> list[dict]:
    """Fetch all comments for a thread, paginating through results."""
    all_comments = []
    for page in range(_MAX_COMMENT_PAGES):
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "tags": f"comment,story_{thread_id}",
            "hitsPerPage": _COMMENTS_PAGE_SIZE,
            "page": page,
        }
        resp = httpx.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"[hn_hiring] Comments page {page}: HTTP {resp.status_code}")
            break
        data = resp.json()
        hits = data.get("hits", [])
        all_comments.extend(hits)
        # Stop if we got fewer than a full page (no more results)
        if len(hits) < _COMMENTS_PAGE_SIZE:
            break
    return all_comments


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 168)  # 1 week for HN

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "hn_hiring").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "hn_hiring", "cached": True}

    # Find latest "Who is hiring?" thread (date-sorted, title-validated)
    thread_id = _find_latest_thread()
    if not thread_id:
        return {"count": 0, "source": "hn_hiring", "error": "no_thread_found"}

    # Fetch all comments with pagination
    comments = _fetch_all_comments(thread_id)

    from normalizers import normalize_hn
    parsed = []
    for c in comments:
        text = c.get("comment_text", "")
        if not text or len(text) < 50:
            continue
        job = parse_hn_comment(text)
        if job:
            parsed.append(job)

    jobs = normalize_hn(parsed, query_hash)

    # Deduplicate by job_hash within batch
    seen = set()
    unique = []
    for j in jobs:
        if j["job_hash"] not in seen:
            seen.add(j["job_hash"])
            unique.append(j)
    jobs = unique

    if jobs:
        now = datetime.utcnow().isoformat()
        for job in jobs:
            job["scraped_at"] = now
        db.table("jobs_raw").upsert(jobs, on_conflict="job_hash").execute()

    logger.info(f"[hn_hiring] {len(jobs)} jobs from {len(comments)} comments (thread {thread_id})")
    return {"count": len(jobs), "source": "hn_hiring"}
