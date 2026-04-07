"""Greenhouse Boards API scraper.

Fetches jobs from companies using Greenhouse ATS via their free public API.
No authentication needed. Board slugs are configurable via SSM parameter.

API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from utils.canonical_hash import canonical_hash

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Default boards — Dublin/Ireland-relevant companies on Greenhouse
DEFAULT_BOARDS = [
    "stripe", "intercom", "mongodb", "twilio", "datadog",
    "pagerduty", "toast", "cloudflare", "elastic", "ripple",
]

# Location keywords to filter for (case-insensitive)
LOCATION_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe", "anywhere"}


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _clean(text):
    """Strip HTML tags and decode entities."""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _location_matches(location_name: str) -> bool:
    """Check if job location matches our target regions."""
    loc_lower = (location_name or "").lower()
    return any(kw in loc_lower for kw in LOCATION_KEYWORDS)


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "greenhouse").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "greenhouse", "cached": True}

    # Read board slugs from SSM (fallback to defaults)
    try:
        import json
        boards_json = get_param("/naukribaba/GREENHOUSE_BOARDS")
        boards = json.loads(boards_json)
    except Exception:
        boards = DEFAULT_BOARDS

    all_jobs = []
    client = httpx.Client(timeout=30)

    for slug in boards:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"[greenhouse] {slug}: HTTP {resp.status_code}")
                continue

            data = resp.json()
            jobs = data.get("jobs", [])

            # Filter by location
            matched = [j for j in jobs if _location_matches(j.get("location", {}).get("name", ""))]
            logger.info(f"[greenhouse] {slug}: {len(matched)}/{len(jobs)} match location filter")

            for j in matched:
                title = (j.get("title") or "").strip()
                location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
                description = _clean(j.get("content") or "")
                apply_url = j.get("absolute_url") or ""

                if not title:
                    continue

                all_jobs.append({
                    "title": title[:500],
                    "company": slug.replace("-", " ").title()[:200],
                    "description": description[:10000],
                    "location": location[:200],
                    "apply_url": apply_url[:1000],
                    "source": "greenhouse",
                    "job_hash": canonical_hash(slug, title, description),
                    "query_hash": query_hash,
                })

        except Exception as e:
            logger.error(f"[greenhouse] {slug} failed: {e}")

    client.close()

    # Dedup within batch
    seen = set()
    unique = []
    for j in all_jobs:
        if j["job_hash"] not in seen:
            seen.add(j["job_hash"])
            unique.append(j)
    all_jobs = unique

    # Write to jobs_raw
    if all_jobs:
        now = datetime.now(timezone.utc).isoformat()
        for job in all_jobs:
            job["scraped_at"] = now
        for i in range(0, len(all_jobs), 50):
            chunk = all_jobs[i:i + 50]
            db.table("jobs_raw").upsert(chunk, on_conflict="job_hash").execute()

    logger.info(f"[greenhouse] {len(all_jobs)} jobs from {len(boards)} boards")
    return {
        "count": len(all_jobs),
        "source": "greenhouse",
        "new_job_hashes": [j["job_hash"] for j in all_jobs],
    }
