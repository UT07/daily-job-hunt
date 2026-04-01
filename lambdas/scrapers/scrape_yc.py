"""YC Jobs scraper — fetches from WorkAtAStartup via Inertia protocol."""
import logging
import re
import json
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

def _get_inertia_version():
    """Fetch the current Inertia version from the WATS HTML page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = httpx.get("https://www.workatastartup.com/jobs", headers=headers, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    match = re.search(r'data-page="(.+?)"', resp.text)
    if not match:
        raise ValueError("Could not find Inertia page data in WATS HTML")
    page_data = json.loads(match.group(1).replace("&quot;", '"'))
    return page_data.get("version", "")

def _fetch_jobs_page(version, query=""):
    """Fetch a page of jobs using the Inertia protocol."""
    headers = {
        "X-Inertia": "true",
        "X-Inertia-Version": version,
        "Accept": "text/html, application/xhtml+xml",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    url = "https://www.workatastartup.com/jobs"
    if query:
        url += f"?query={query}"
    resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    if "json" not in ct:
        raise ValueError(f"Expected JSON response, got {ct}")
    data = resp.json()
    return data.get("props", {}).get("jobs", [])

def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 48)

    db = get_supabase()

    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "yc").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "yc", "cached": True}

    from normalizers import normalize_generic_web

    # Get current Inertia version (changes on each deploy)
    try:
        version = _get_inertia_version()
        logger.info(f"[yc] Got Inertia version: {version[:20]}...")
    except Exception as e:
        logger.error(f"[yc] Failed to get Inertia version: {e}")
        return {"count": 0, "source": "yc", "error": str(e)}

    all_raw = []
    for query in queries:
        try:
            raw_jobs = _fetch_jobs_page(version, query)
            logger.info(f"[yc] Query '{query}': {len(raw_jobs)} raw jobs")
            for job in raw_jobs:
                # Map WATS fields to our generic normalizer format
                # WATS now returns flat job objects (not nested under companies)
                company_slug = job.get("companySlug", "")
                job_id = job.get("id", "")
                all_raw.append({
                    "title": job.get("title", ""),
                    "company": job.get("companyName", ""),
                    "description": job.get("companyOneLiner", ""),  # No full description in listing
                    "location": job.get("location", ""),
                    "url": f"https://www.workatastartup.com/companies/{company_slug}/jobs/{job_id}" if company_slug and job_id else "",
                    "jobType": job.get("jobType", ""),
                })
        except Exception as e:
            logger.warning(f"[yc] Query '{query}' failed: {e}")

    jobs = normalize_generic_web(all_raw, "yc", query_hash)
    if jobs:
        now = datetime.utcnow().isoformat()
        for job in jobs:
            job["scraped_at"] = now
        db.table("jobs_raw").upsert(jobs, on_conflict="job_hash").execute()

    logger.info(f"[yc] {len(jobs)} jobs total")
    return {"count": len(jobs), "source": "yc"}
