"""LinkedIn job scraper using Bright Data Web Unlocker.

Scrapes public LinkedIn job search pages via httpx + Web Unlocker proxy.
Web Unlocker handles all anti-bot: CAPTCHAs, fingerprints, sessions.
No browser needed — just HTTP requests.
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


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _clean_html(text):
    """Strip HTML tags and decode entities."""
    text = html.unescape(text or "")
    text = re.sub(r'<[^>]+>', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _make_hash(company, title, desc):
    return canonical_hash(company, title, desc)


def _parse_search_page(html_text):
    """Extract job cards from LinkedIn search results HTML."""
    jobs = []

    # Job IDs from data-entity-urn (most reliable on LinkedIn public pages)
    job_ids = re.findall(r'urn:li:jobPosting:(\d+)', html_text)
    # Titles from the h3.base-search-card__title element
    titles = re.findall(
        r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*(.*?)\s*</h3>',
        html_text, re.DOTALL
    )
    # Company names from h4.base-search-card__subtitle > a
    company_matches = re.findall(
        r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>',
        html_text, re.DOTALL
    )
    # Locations
    location_matches = re.findall(
        r'<span class="[^"]*job-search-card__location[^"]*">\s*(.*?)\s*</span>',
        html_text, re.DOTALL
    )

    # Match up the parallel arrays
    for i in range(min(len(job_ids), len(titles))):
        job_id = job_ids[i]
        title = _clean_html(titles[i]) if i < len(titles) else ""
        company = _clean_html(company_matches[i]) if i < len(company_matches) else ""
        location = _clean_html(location_matches[i]) if i < len(location_matches) else ""

        if not title or not company:
            continue

        jobs.append({
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "apply_url": f"https://www.linkedin.com/jobs/view/{job_id}",
        })

    return jobs


def _fetch_job_detail(job_id, proxy_url):
    """Fetch full job description from LinkedIn detail page."""
    # First try the lightweight guest API endpoint
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
        
        # If blocked or failed, fallback to standard web view
        if resp.status_code != 200:
            url = f"https://www.linkedin.com/jobs/view/{job_id}"
            resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
            
        if resp.status_code != 200:
            return None

        text = resp.text
        # Extract description from multiple possible selectors
        desc_match = re.search(
            r'<div class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>\s*</div>',
            text, re.DOTALL
        )
        if not desc_match:
            desc_match = re.search(
                r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
                text, re.DOTALL
            )
        if not desc_match:
            # Fallback specifically for the jobs-guest fragment HTML wrapper
            desc_match = re.search(
                r'<div class="[^"]*description__text[^"]*"[^>]*>(.*?)$',
                text, re.DOTALL
            )
            
        if desc_match:
            return _clean_html(desc_match.group(1))
            
        # If it's a small fragment (like the jobs-guest response), return it all
        if "description__text" in text and len(text) < 15000:
            return _clean_html(text)
            
    except Exception as e:
        logger.warning(f"[linkedin] Detail fetch failed for {job_id}: {e}")
    return None


def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    location = event.get("location", "Ireland")
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)
    max_jobs = event.get("max_jobs", 50)

    db = get_supabase()
    proxy_url = get_param("/naukribaba/PROXY_URL")

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "linkedin").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "linkedin", "cached": True}

    from normalizers import normalize_job
    all_jobs = []

    for query in queries:
        url = f"https://www.linkedin.com/jobs/search?keywords={query}&location={location}&start=0"
        try:
            resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
            if resp.status_code != 200:
                logger.warning(f"[linkedin] Search returned HTTP {resp.status_code}")
                continue

            cards = _parse_search_page(resp.text)
            logger.info(f"[linkedin] Query '{query}': {len(cards)} cards found")

            for card in cards[:max_jobs - len(all_jobs)]:
                # Fetch full description
                full_desc = _fetch_job_detail(card["job_id"], proxy_url)
                desc_quality = "full" if full_desc else "snippet"
                description = full_desc or card.get("title", "")  # fallback to title

                job = normalize_job({
                    "title": card["title"],
                    "company": card["company"],
                    "description": description,
                    "location": card["location"],
                    "url": card["apply_url"],
                }, source="linkedin", query_hash=query_hash)

                if job:
                    job["description_quality"] = desc_quality
                    all_jobs.append(job)

                if len(all_jobs) >= max_jobs:
                    break

        except Exception as e:
            logger.error(f"[linkedin] Query '{query}' failed: {e}")

        if len(all_jobs) >= max_jobs:
            break

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
            job.pop("description_quality", None)  # not in DB schema yet
        db.table("jobs_raw").upsert(all_jobs, on_conflict="job_hash").execute()

    logger.info(f"[linkedin] {len(all_jobs)} jobs saved")
    return {"count": len(all_jobs), "source": "linkedin",
            "new_job_hashes": [j["job_hash"] for j in all_jobs]}
