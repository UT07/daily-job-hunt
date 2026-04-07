"""Adzuna REST API scraper."""
import json
import logging
import re
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Adzuna API snippets are capped at ~500 chars and end with "…".
# Jobs below this threshold get a detail-page fetch attempt.
_TRUNCATION_THRESHOLD = 600


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _fetch_full_description(job_id: str, country: str) -> str | None:
    """Fetch the full job description from the Adzuna detail page.

    Tries JSON-LD first (most reliable), then falls back to a regex
    pattern against the rendered HTML. Returns None on any failure.
    """
    url = f"https://www.adzuna.co.uk/jobs/details/{job_id}"
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NaukriBaba/1.0)"
        })
        if resp.status_code != 200:
            logger.debug(f"[adzuna] detail fetch {job_id}: HTTP {resp.status_code}")
            return None

        html = resp.text

        # Strategy 1: JSON-LD application/ld+json block
        ld_match = re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        if ld_match:
            try:
                data = json.loads(ld_match.group(1))
                # May be a list or a single object
                if isinstance(data, list):
                    data = data[0]
                desc = data.get("description") or ""
                if desc and len(desc) > _TRUNCATION_THRESHOLD:
                    # Strip HTML tags from LD+JSON descriptions
                    desc = re.sub(r'<[^>]+>', ' ', desc).strip()
                    desc = re.sub(r'\s+', ' ', desc)
                    return desc
            except (json.JSONDecodeError, IndexError, AttributeError):
                pass

        # Strategy 2: adz-body or job-description div (Adzuna page structure)
        for pattern in [
            r'<div[^>]+class="[^"]*adz-job-body[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]+class="[^"]*job-description[^"]*"[^>]*>(.*?)</div>',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                raw = m.group(1)
                text = re.sub(r'<[^>]+>', ' ', raw).strip()
                text = re.sub(r'\s+', ' ', text)
                if len(text) > _TRUNCATION_THRESHOLD:
                    return text

    except Exception as exc:
        logger.debug(f"[adzuna] detail fetch {job_id}: {exc}")

    return None


def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "adzuna").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "adzuna", "cached": True}

    app_id = get_param("/naukribaba/ADZUNA_APP_ID")
    app_key = get_param("/naukribaba/ADZUNA_APP_KEY")

    from normalizers import normalize_adzuna
    all_raw: list[dict] = []

    # NOTE: Ireland ("ie") is NOT a supported Adzuna country.
    # Supported: at, au, be, br, ca, ch, de, es, fr, gb, in, it, mx, nl, nz, pl, sg, us, za.
    # We use "gb" (UK) as the closest market for Ireland-based searches.
    country = event.get("country", "gb")
    max_days_old = event.get("max_days_old", 7)

    for query in queries:
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": query,
            "max_days_old": max_days_old,
            "results_per_page": 50,
        }
        resp = httpx.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            all_raw.extend(results)
            logger.info(f"[adzuna] Query '{query}' ({country}): {len(results)} raw results")
        else:
            logger.warning(f"[adzuna] Query '{query}' ({country}): HTTP {resp.status_code} - {resp.text[:200]}")

    # --- Enrich truncated descriptions before normalizing ---
    enriched = 0
    truncated_unfixable = 0
    for item in all_raw:
        desc = item.get("description") or ""
        if len(desc) < _TRUNCATION_THRESHOLD:
            job_id = str(item.get("id", ""))
            if job_id:
                full_desc = _fetch_full_description(job_id, country)
                if full_desc:
                    item["description"] = full_desc
                    enriched += 1
                else:
                    # Keep snippet but flag it so downstream can treat it as low-quality
                    item["description"] = desc
                    item["_description_truncated"] = True
                    truncated_unfixable += 1

    if enriched or truncated_unfixable:
        logger.info(f"[adzuna] Description enrichment: {enriched} enriched, {truncated_unfixable} still truncated")

    # Build a lookup of (company, title) pairs whose description is still truncated.
    # normalize_adzuna may skip some raw items (missing title/company), so position-
    # based indexing would mis-align. Matching on company+title is safe here because
    # Adzuna rarely has two jobs with identical company+title in a single result set.
    truncated_keys: set[tuple[str, str]] = set()
    for item in all_raw:
        if item.get("_description_truncated"):
            company = (item.get("company") or {}).get("display_name", "").lower().strip()
            title = (item.get("title") or "").lower().strip()
            truncated_keys.add((company, title))

    all_jobs = normalize_adzuna(all_raw, query_hash)

    # Propagate the truncation flag to normalized jobs
    if truncated_keys:
        for job in all_jobs:
            key = (job.get("company", "").lower().strip(), job.get("title", "").lower().strip())
            if key in truncated_keys:
                job["description_truncated"] = True

    # Deduplicate by job_hash within batch (multiple queries may return the same job,
    # and Supabase upsert fails on intra-batch duplicates)
    seen_hashes = set()
    unique_jobs = []
    for job in all_jobs:
        if job["job_hash"] not in seen_hashes:
            seen_hashes.add(job["job_hash"])
            unique_jobs.append(job)
    all_jobs = unique_jobs

    # Write to jobs_raw (bulk upsert).
    # Strip description_truncated before persisting — it's not a jobs_raw column.
    # The flag was used only to propagate the marking above; truncated jobs are
    # implicitly identifiable by description length < _TRUNCATION_THRESHOLD.
    if all_jobs:
        now = datetime.utcnow().isoformat()
        for job in all_jobs:
            job["scraped_at"] = now
            job.pop("description_truncated", None)
        db.table("jobs_raw").upsert(all_jobs, on_conflict="job_hash").execute()

    return {"count": len(all_jobs), "source": "adzuna"}
