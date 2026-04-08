"""Irish job portal scraper — Jobs.ie, IrishJobs, GradIreland.

Jobs.ie and IrishJobs migrated to a StepStone React platform (2025+).
Uses data-testid attributes and h2 > a patterns for extraction.
Detail pages on IrishJobs return 403 without proxy — routed through
Bright Data Web Unlocker as fallback.
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _clean(text):
    text = html.unescape(text or "")
    text = re.sub(r'<[^>]+>', ' ', text)
    return text.strip()


def _make_hash(company, title, desc):
    return canonical_hash(company, title, desc)


def _extract_description(html_text):
    """Extract job description from a StepStone detail page HTML.

    Tries three strategies in order:
      1. data-testid HTML attributes (StepStone React components)
      2. JSON-LD structured data (application/ld+json JobPosting)
      3. Generic job-description class patterns
    """
    import json as _json

    detail_clean = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL)

    # Strategy 1: StepStone data-testid attributes
    for pattern in [
        r'data-testid="vacancy-description"[^>]*>(.*?)</div>\s*</div>',
        r'data-testid="job-description"[^>]*>(.*?)</div>\s*</div>',
    ]:
        match = re.search(pattern, detail_clean, re.DOTALL)
        if match and len(match.group(1)) > 100:
            return _clean(match.group(1))

    # Strategy 2: JSON-LD structured data (Jobs.ie uses this reliably)
    ld_blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text, re.DOTALL,
    )
    for block in ld_blocks:
        try:
            data = _json.loads(block)
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                desc = _clean(data.get("description", ""))
                if len(desc) > 100:
                    return desc
        except (ValueError, KeyError, TypeError):
            continue

    # Strategy 3: Generic class-based patterns
    for pattern in [
        r'class="[^"]*job-description[^"]*"[^>]*>(.*?)</div>',
    ]:
        match = re.search(pattern, detail_clean, re.DOTALL)
        if match and len(match.group(1)) > 100:
            return _clean(match.group(1))

    return ""


def _fetch_detail_page(link, search_url, proxy_url=None):
    """Fetch a StepStone detail page, trying direct first then proxy.

    Returns (description, quality) where quality is 'full', 'snippet', or 'none'.
    """
    detail_headers = {
        **HEADERS,
        "Referer": search_url,
    }

    # Attempt 1: direct request (works for Jobs.ie, often blocked on IrishJobs)
    try:
        resp = httpx.get(link, headers=detail_headers, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            desc = _extract_description(resp.text)
            if desc:
                return desc, "full"
        elif resp.status_code == 403:
            logger.info(f"[irish] Direct detail fetch returned 403 for {link}, trying proxy")
        else:
            logger.warning(f"[irish] Direct detail fetch returned HTTP {resp.status_code} for {link}")
    except Exception as e:
        logger.warning(f"[irish] Direct detail fetch failed for {link}: {e}")

    # Attempt 2: route through Web Unlocker proxy
    if proxy_url:
        try:
            resp = httpx.get(
                link, headers=detail_headers, proxy=proxy_url,
                timeout=30, follow_redirects=True, verify=False,
            )
            if resp.status_code == 200:
                desc = _extract_description(resp.text)
                if desc:
                    return desc, "full"
                logger.warning(f"[irish] Proxy got 200 but no description extracted for {link}")
            else:
                logger.warning(f"[irish] Proxy detail fetch returned HTTP {resp.status_code} for {link}")
        except Exception as e:
            logger.warning(f"[irish] Proxy detail fetch failed for {link}: {e}")

    return "", "none"


def _scrape_stepstone_site(site_key, base_url, queries, client, proxy_url=None):
    """Scrape Jobs.ie or IrishJobs (StepStone platform). Returns list of job dicts."""
    jobs = []
    detail_stats = {"full": 0, "none": 0}
    for query in queries:
        slug = query.lower().replace(" ", "-")
        search_url = f"{base_url}/jobs/{slug}"
        try:
            resp = client.get(search_url, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[{site_key}] HTTP {resp.status_code} for {query}")
                continue

            page = resp.text
            # Strip style tags for cleaner parsing
            clean_page = re.sub(r'<style[^>]*>.*?</style>', '', page, flags=re.DOTALL)

            # Extract parallel arrays from StepStone card structure
            job_ids = re.findall(r'id="job-item-(\d+)"', clean_page)
            companies = re.findall(r'COMPANY_LOGO_IMAGE" alt="([^"]+)"', clean_page)
            # Titles from h2 > a[href="/job/..."]
            title_links = re.findall(
                r'<h2[^>]*>\s*<a[^>]*href="(/job/[^"]+)"[^>]*>(.*?)</a>',
                clean_page, re.DOTALL
            )

            for i in range(min(len(job_ids), len(title_links), 50)):
                href, inner_html = title_links[i]
                title = _clean(inner_html)
                company = _clean(companies[i]) if i < len(companies) else ""
                link = f"{base_url}{href}"

                if not title or not company:
                    continue

                # Fetch detail page for description (direct first, proxy fallback)
                description, desc_quality = _fetch_detail_page(link, search_url, proxy_url)
                detail_stats[desc_quality] = detail_stats.get(desc_quality, 0) + 1

                jobs.append({
                    "title": title[:500],
                    "company": company[:200],
                    "description": description[:10000],
                    "location": "Ireland",
                    "apply_url": link[:1000],
                    "source": site_key,
                })

            logger.info(f"[{site_key}] Query '{query}': {len(job_ids)} jobs found")
        except Exception as e:
            logger.error(f"[{site_key}] Query '{query}' failed: {e}")

    logger.info(f"[{site_key}] Detail page results: {detail_stats}")
    return jobs


def _scrape_gradireland(queries, client):
    """Scrape GradIreland via their internal JSON search API.

    GradIreland is a Gatsby SPA — HTML scraping returns 0 results because
    job listings are client-rendered. The site uses a JSON search service at
    POST /ext/svc/inferno-search-service-1-0/search with an X-Host header.

    API reverse-engineered from the webpack bundle (module 67464,
    chunk 2035/3336) on 2026-04-08. Key observations:
      - Endpoint: POST https://gradireland.com/ext/svc/inferno-search-service-1-0/search
      - Required header: X-Host: users.gradireland.com
      - keys must be an array: [full_query, word1, word2, ...]
      - conditionGroup filters to type=opportunity (job listings only)
      - conditions with last_published <= now is required for results to appear
      - Response: data.search.documents[] with title, organisation.title,
        location, body (HTML), applicationUrl, path
    """
    import json as _json

    _BASE = "https://gradireland.com"
    _API_URL = f"{_BASE}/ext/svc/inferno-search-service-1-0/search"
    _API_HEADERS = {
        **HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": _BASE,
        "X-Host": "users.gradireland.com",
    }

    def _build_keys(query: str) -> list[str]:
        """Replicate JS: [query, word1, word2, ...] from the query string."""
        words = query.split()
        return [query] + words if words else [""]

    def _doc_to_job(doc: dict) -> dict | None:
        """Convert an API document to a job dict."""
        title = (doc.get("title") or "").strip()
        if not title:
            return None
        org = doc.get("organisation") or {}
        company = (org.get("title") or "").strip() if isinstance(org, dict) else ""
        location = (doc.get("location") or "Ireland").strip()
        description = _clean(doc.get("body") or "")
        # Use applicationUrl if present, else build from path
        apply_url = (doc.get("applicationUrl") or "").strip()
        if not apply_url:
            path = doc.get("path") or ""
            apply_url = f"{_BASE}{path}" if path else ""
        return {
            "title": title[:500],
            "company": company[:200],
            "description": description[:10000],
            "location": location[:200],
            "apply_url": apply_url[:1000],
            "source": "gradireland",
        }

    jobs = []
    seen_urls: set[str] = set()

    for query in queries:
        # The conditions filter requires last_published <= now; without it the API
        # returns 0 results. Use current UTC time in ISO format.
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = {
            "keys": _build_keys(query),
            "limit": 50,
            "offset": 0,
            "conditionGroup": {
                "conjunction": "AND",
                "groups": [
                    {
                        "conjunction": "OR",
                        "conditions": [{"name": "type", "value": "opportunity"}],
                    }
                ],
            },
            "conditions": [
                {"name": "last_published", "value": now_iso, "operator": "<="}
            ],
            "includePromoted": True,
            "sort": [{"direction": "desc", "id": "search_api_relevance"}],
        }

        try:
            resp = client.post(
                _API_URL, headers=_API_HEADERS,
                content=_json.dumps(payload), timeout=20,
            )
            if resp.status_code != 200:
                logger.warning(f"[gradireland] HTTP {resp.status_code} for query '{query}'")
                continue

            data = resp.json()
            documents = (data.get("search") or {}).get("documents") or []
            result_count = (data.get("search") or {}).get("result_count", 0)

            query_jobs = []
            for doc in documents:
                job = _doc_to_job(doc)
                if not job:
                    continue
                url = job["apply_url"]
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                query_jobs.append(job)

            logger.info(
                f"[gradireland] Query '{query}': {len(query_jobs)} jobs "
                f"(API total={result_count})"
            )
            jobs.extend(query_jobs)

        except Exception as e:
            logger.error(f"[gradireland] Query '{query}' failed: {e}")

    return jobs


def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache across all Irish sources
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .in_("source", ["jobs_ie", "irishjobs", "gradireland"]) \
        .eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "irish_portals", "cached": True}

    # Fetch proxy URL for detail page fallback (IrishJobs blocks direct detail requests)
    try:
        proxy_url = get_param("/naukribaba/PROXY_URL")
    except Exception as e:
        logger.warning(f"[irish_portals] Could not fetch PROXY_URL, detail pages may fail: {e}")
        proxy_url = None

    all_jobs = []
    client = httpx.Client()

    # Jobs.ie and IrishJobs use the same StepStone platform
    all_jobs.extend(_scrape_stepstone_site("jobs_ie", "https://www.jobs.ie", queries, client, proxy_url))
    all_jobs.extend(_scrape_stepstone_site("irishjobs", "https://www.irishjobs.ie", queries, client, proxy_url))
    all_jobs.extend(_scrape_gradireland(queries, client))

    client.close()

    # Dedup within batch
    seen = set()
    unique = []
    for j in all_jobs:
        h = _make_hash(j["company"], j["title"], j["description"])
        if h not in seen:
            seen.add(h)
            j["job_hash"] = h
            j["query_hash"] = query_hash
            unique.append(j)
    all_jobs = unique

    if all_jobs:
        now = datetime.now(timezone.utc).isoformat()
        for job in all_jobs:
            job["scraped_at"] = now
        for i in range(0, len(all_jobs), 50):
            chunk = all_jobs[i:i+50]
            db.table("jobs_raw").upsert(chunk, on_conflict="job_hash").execute()

    logger.info(f"[irish_portals] {len(all_jobs)} total jobs across 3 sites")
    return {"count": len(all_jobs), "source": "irish_portals",
            "new_job_hashes": [j["job_hash"] for j in all_jobs]}
