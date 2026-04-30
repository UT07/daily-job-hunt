"""Ashby HQ Boards API scraper.

Fetches jobs from companies using Ashby ATS via their free public posting API.
No authentication needed. Company slugs are configurable via SSM parameter.

API: GET https://api.ashbyhq.com/posting-api/job-board/{company}
Board UI: https://jobs.ashbyhq.com/{company}
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

# Default companies — Dublin/Ireland-relevant startups on Ashby
DEFAULT_COMPANIES = [
    "anthropic", "linear", "vercel", "notion", "figma", "retool",
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


def _location_matches(location_name: str, is_remote: bool) -> bool:
    """Check if job location matches our target regions."""
    if is_remote:
        return True
    loc_lower = (location_name or "").lower()
    return any(kw in loc_lower for kw in LOCATION_KEYWORDS)


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "ashby").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "ashby", "cached": True}

    # Read company slugs from SSM (fallback to defaults)
    try:
        import json
        companies_json = get_param("/naukribaba/ASHBY_COMPANIES")
        companies = json.loads(companies_json)
    except Exception:
        companies = DEFAULT_COMPANIES

    all_jobs = []
    client = httpx.Client(timeout=30)

    for company in companies:
        try:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"[ashby] {company}: HTTP {resp.status_code}")
                continue

            data = resp.json()
            jobs = data.get("jobs", [])

            # Filter by location (Ashby also has isRemote at the top level per job)
            matched = [
                j for j in jobs
                if _location_matches(
                    j.get("location", "") if isinstance(j.get("location"), str)
                    else (j.get("location") or {}).get("name", ""),
                    j.get("isRemote", False),
                )
            ]
            logger.info(f"[ashby] {company}: {len(matched)}/{len(jobs)} match location filter")

            for j in matched:
                title = (j.get("title") or "").strip()

                # Location may be a string or an object depending on API version
                loc = j.get("location", "")
                if isinstance(loc, dict):
                    location = loc.get("name", "")
                else:
                    location = loc or ""
                if j.get("isRemote"):
                    location = location or "Remote"

                description = _clean(j.get("descriptionHtml") or j.get("description") or "")
                apply_url = j.get("jobUrl") or j.get("applyUrl") or f"https://jobs.ashbyhq.com/{company}"

                if not title:
                    continue

                # Use the company slug to derive a readable company name; the API
                # also returns organizationName at the top level of the response.
                company_name = (data.get("organizationName") or company.replace("-", " ").title())

                # Ashby's job-board API exposes posting timestamp as
                # `publishedAt` (canonical) or `updatedAt` (fallback).
                from normalizers import _parse_posted_date
                posted_date = _parse_posted_date(
                    j.get("publishedAt") or j.get("updatedAt") or j.get("createdAt")
                )

                all_jobs.append({
                    "title": title[:500],
                    "company": company_name[:200],
                    "description": description[:10000],
                    "location": location[:200],
                    "apply_url": apply_url[:1000],
                    "source": "ashby",
                    "job_hash": canonical_hash(company_name, title, description),
                    "query_hash": query_hash,
                    "posting_id": str(j.get("id", "")),
                    "company_slug": company,
                    "posted_date": posted_date,
                })

        except Exception as e:
            logger.error(f"[ashby] {company} failed: {e}")

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

    logger.info(f"[ashby] {len(all_jobs)} jobs from {len(companies)} companies")
    return {
        "count": len(all_jobs),
        "source": "ashby",
        "new_job_hashes": [j["job_hash"] for j in all_jobs],
    }
