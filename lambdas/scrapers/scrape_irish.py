"""Irish job portal scraper — Jobs.ie, IrishJobs, GradIreland.

Jobs.ie and IrishJobs migrated to a StepStone React platform (2025+).
Uses data-testid attributes and h2 > a patterns for extraction.
No proxy needed — simple HTML sites with no anti-bot.
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


def _scrape_stepstone_site(site_key, base_url, queries, client):
    """Scrape Jobs.ie or IrishJobs (StepStone platform). Returns list of job dicts."""
    jobs = []
    for query in queries:
        slug = query.lower().replace(" ", "-")
        url = f"{base_url}/jobs/{slug}"
        try:
            resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
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

                # Fetch detail page for description
                description = ""
                try:
                    detail = client.get(link, headers=HEADERS, timeout=15, follow_redirects=True)
                    if detail.status_code == 200:
                        detail_clean = re.sub(r'<style[^>]*>.*?</style>', '', detail.text, flags=re.DOTALL)
                        # StepStone uses data-testid="vacancy-description" or similar
                        for pattern in [
                            r'data-testid="vacancy-description"[^>]*>(.*?)</div>\s*</div>',
                            r'data-testid="job-description"[^>]*>(.*?)</div>\s*</div>',
                            r'class="[^"]*job-description[^"]*"[^>]*>(.*?)</div>',
                        ]:
                            match = re.search(pattern, detail_clean, re.DOTALL)
                            if match and len(match.group(1)) > 100:
                                description = _clean(match.group(1))
                                break
                except Exception:
                    pass

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

    return jobs


def _scrape_gradireland(queries, client):
    """Scrape GradIreland (Drupal/views-based)."""
    jobs = []
    for query in queries:
        url = f"https://gradireland.com/graduate-jobs?keyword={query.replace(' ', '+')}"
        try:
            resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[gradireland] HTTP {resp.status_code} for {query}")
                continue

            page = resp.text
            titles = [_clean(m) for m in re.findall(
                r'<h\d[^>]*class="[^"]*field--name-title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>', page
            )]
            companies = [_clean(m) for m in re.findall(
                r'<div[^>]*class="[^"]*field--name-field-company[^"]*"[^>]*>([^<]+)</div>', page
            )]
            locations = [_clean(m) for m in re.findall(
                r'<div[^>]*class="[^"]*field--name-field-location[^"]*"[^>]*>([^<]+)</div>', page
            )]
            links = re.findall(
                r'<h\d[^>]*class="[^"]*field--name-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"', page
            )

            for i in range(min(len(titles), 50)):
                title = titles[i]
                if not title:
                    continue
                company = companies[i] if i < len(companies) else ""
                location = locations[i] if i < len(locations) else "Ireland"
                link = links[i] if i < len(links) else ""
                if link and not link.startswith("http"):
                    link = f"https://gradireland.com{link}"

                jobs.append({
                    "title": title[:500],
                    "company": (company or "gradireland")[:200],
                    "description": "",
                    "location": location[:200],
                    "apply_url": link[:1000],
                    "source": "gradireland",
                })

            logger.info(f"[gradireland] Query '{query}': {len(titles)} jobs found")
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

    all_jobs = []
    client = httpx.Client()

    # Jobs.ie and IrishJobs use the same StepStone platform
    all_jobs.extend(_scrape_stepstone_site("jobs_ie", "https://www.jobs.ie", queries, client))
    all_jobs.extend(_scrape_stepstone_site("irishjobs", "https://www.irishjobs.ie", queries, client))
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
