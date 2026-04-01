"""Indeed job scraper using Bright Data Web Unlocker.

Scrapes public Indeed job search pages via httpx + Web Unlocker proxy.
Primary strategy: extract structured data from window.mosaic.providerData JSON.
Fallback: parse HTML job cards from search results.
"""
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

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


def _parse_mosaic_json(html_text):
    """Extract jobs from Indeed's window.mosaic.providerData embedded JSON.

    Indeed embeds structured job data in a JS variable. This is the richest
    source of data including full descriptions when available.
    """
    jobs = []

    # Look for the mosaic provider data JSON blob
    mosaic_match = re.search(
        r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});\s*$',
        html_text, re.MULTILINE
    )
    if not mosaic_match:
        # Try alternate pattern
        mosaic_match = re.search(
            r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});',
            html_text, re.DOTALL
        )

    if not mosaic_match:
        return jobs

    try:
        data = json.loads(mosaic_match.group(1))
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[indeed] Failed to parse mosaic JSON: {e}")
        return jobs

    # Navigate the nested structure to find job results
    results = []
    # Common paths in the mosaic data
    meta_data = data.get("metaData", {})
    models = meta_data.get("mosaicProviderJobCardsModel", {})
    results = models.get("results", [])

    if not results:
        # Try alternate path
        results = data.get("results", [])

    for item in results:
        title = item.get("title") or item.get("displayTitle") or ""
        company = item.get("company") or item.get("companyName") or ""
        location = item.get("formattedLocation") or item.get("jobLocationCity") or ""
        job_key = item.get("jobkey") or item.get("jk") or ""
        description = item.get("snippet") or item.get("description") or ""

        # Some mosaic responses include full HTML description
        if item.get("jobDescription"):
            description = item["jobDescription"]

        if not title or not company:
            continue

        apply_url = f"https://www.indeed.com/viewjob?jk={job_key}" if job_key else ""

        jobs.append({
            "title": _clean_html(title),
            "company": _clean_html(company),
            "location": _clean_html(location),
            "description": _clean_html(description),
            "apply_url": apply_url,
            "job_key": job_key,
        })

    return jobs


def _parse_html_cards(html_text):
    """Fallback: parse job cards from Indeed search results HTML."""
    jobs = []

    # Extract job cards — Indeed uses job_seen_beacon or resultContent classes
    # Extract job keys from data attributes or links
    job_keys = re.findall(r'data-jk="([a-f0-9]+)"', html_text)
    if not job_keys:
        job_keys = re.findall(r'jk=([a-f0-9]+)', html_text)

    # Extract titles from job title links
    title_matches = re.findall(
        r'<(?:a|h2)[^>]*class="[^"]*jobTitle[^"]*"[^>]*>.*?<span[^>]*>([^<]+)</span>',
        html_text, re.DOTALL
    )
    if not title_matches:
        title_matches = re.findall(
            r'<a[^>]*id="job_([^"]*)"[^>]*>.*?<span[^>]*>([^<]+)</span>',
            html_text, re.DOTALL
        )
        title_matches = [t[1] if isinstance(t, tuple) else t for t in title_matches]

    # Company names
    company_matches = re.findall(
        r'<span[^>]*data-testid="company-name"[^>]*>([^<]+)</span>',
        html_text
    )
    if not company_matches:
        company_matches = re.findall(
            r'<span class="[^"]*companyName[^"]*">([^<]+)</span>',
            html_text
        )

    # Locations
    location_matches = re.findall(
        r'<div[^>]*data-testid="text-location"[^>]*>([^<]+)</div>',
        html_text
    )
    if not location_matches:
        location_matches = re.findall(
            r'<div class="[^"]*companyLocation[^"]*">([^<]+)</div>',
            html_text
        )

    # Snippets
    snippet_matches = re.findall(
        r'<div class="[^"]*job-snippet[^"]*"[^>]*>(.*?)</div>',
        html_text, re.DOTALL
    )

    for i in range(min(len(job_keys), len(title_matches))):
        title = _clean_html(title_matches[i]) if i < len(title_matches) else ""
        company = _clean_html(company_matches[i]) if i < len(company_matches) else ""
        location = _clean_html(location_matches[i]) if i < len(location_matches) else ""
        snippet = _clean_html(snippet_matches[i]) if i < len(snippet_matches) else ""
        job_key = job_keys[i]

        if not title or not company:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "description": snippet,
            "apply_url": f"https://www.indeed.com/viewjob?jk={job_key}",
            "job_key": job_key,
        })

    return jobs


def _fetch_job_detail(job_key, proxy_url):
    """Fetch full job description from Indeed detail page."""
    url = f"https://www.indeed.com/viewjob?jk={job_key}"
    try:
        resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
        if resp.status_code != 200:
            return None

        text = resp.text

        # Try to extract description from the detail page JSON
        desc_json_match = re.search(
            r'"jobDescriptionText"\s*:\s*"((?:[^"\\]|\\.)*)"',
            text
        )
        if desc_json_match:
            try:
                raw = desc_json_match.group(1)
                decoded = raw.encode().decode('unicode_escape')
                return _clean_html(decoded)
            except Exception:
                pass

        # Fallback: extract from HTML
        desc_match = re.search(
            r'<div[^>]*id="jobDescriptionText"[^>]*>(.*?)</div>',
            text, re.DOTALL
        )
        if desc_match:
            return _clean_html(desc_match.group(1))

    except Exception as e:
        logger.warning(f"[indeed] Detail fetch failed for {job_key}: {e}")
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
        .eq("source", "indeed").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "indeed", "cached": True}

    from normalizers import normalize_job
    all_jobs = []

    for query in queries:
        encoded_query = quote_plus(query)
        encoded_location = quote_plus(location)
        url = f"https://www.indeed.com/jobs?q={encoded_query}&l={encoded_location}&start=0"

        try:
            resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
            if resp.status_code != 200:
                logger.warning(f"[indeed] Search returned HTTP {resp.status_code}")
                continue

            # Primary: try mosaic JSON extraction
            cards = _parse_mosaic_json(resp.text)
            extraction_method = "mosaic_json"

            # Fallback: parse HTML cards
            if not cards:
                cards = _parse_html_cards(resp.text)
                extraction_method = "html_cards"

            logger.info(f"[indeed] Query '{query}': {len(cards)} cards via {extraction_method}")

            for card in cards[:max_jobs - len(all_jobs)]:
                # Fetch full description if we only have a snippet
                full_desc = None
                if card.get("job_key") and len(card.get("description", "")) < 200:
                    full_desc = _fetch_job_detail(card["job_key"], proxy_url)

                desc_quality = "full" if full_desc or len(card.get("description", "")) > 500 else "snippet"
                description = full_desc or card.get("description", "")

                job = normalize_job({
                    "title": card["title"],
                    "company": card["company"],
                    "description": description,
                    "location": card["location"],
                    "url": card["apply_url"],
                }, source="indeed", query_hash=query_hash)

                if job:
                    job["description_quality"] = desc_quality
                    all_jobs.append(job)

                if len(all_jobs) >= max_jobs:
                    break

        except Exception as e:
            logger.error(f"[indeed] Query '{query}' failed: {e}")

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
        db.table("jobs_raw").upsert(all_jobs, on_conflict="job_hash").execute()

    logger.info(f"[indeed] {len(all_jobs)} jobs saved")
    return {"count": len(all_jobs), "source": "indeed",
            "new_job_hashes": [j["job_hash"] for j in all_jobs]}
