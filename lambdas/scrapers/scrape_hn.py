"""HN Hiring scraper — fetches latest 'Who is hiring?' thread via Algolia API."""
import logging
import re
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

def parse_hn_comment(text: str) -> dict:
    """Parse a HN hiring comment into job fields."""
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r'<[^>]+>', '\n', text).strip()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
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

    # Find latest "Who is hiring?" thread
    search_url = "https://hn.algolia.com/api/v1/search"
    params = {"query": "Ask HN: Who is hiring?", "tags": "story", "hitsPerPage": 1}
    resp = httpx.get(search_url, params=params, timeout=15)
    if resp.status_code != 200:
        logger.warning(f"[hn_hiring] Thread search: HTTP {resp.status_code}")
        return {"count": 0, "source": "hn_hiring", "error": f"http_{resp.status_code}"}
    hits = resp.json().get("hits", [])
    if not hits:
        return {"count": 0, "source": "hn_hiring", "error": "no_thread_found"}

    thread_id = hits[0]["objectID"]

    # Fetch comments
    comments_url = f"https://hn.algolia.com/api/v1/search"
    params = {"tags": f"comment,story_{thread_id}", "hitsPerPage": 200}
    resp = httpx.get(comments_url, params=params, timeout=30)
    if resp.status_code != 200:
        logger.warning(f"[hn_hiring] Comments fetch: HTTP {resp.status_code}")
        return {"count": 0, "source": "hn_hiring", "error": f"http_{resp.status_code}"}
    comments = resp.json().get("hits", [])

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

    logger.info(f"[hn_hiring] {len(jobs)} jobs from {len(comments)} comments")
    return {"count": len(jobs), "source": "hn_hiring"}
