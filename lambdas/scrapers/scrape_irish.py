"""Irish job portal scraper — Jobs.ie, IrishJobs, GradIreland.

Simple HTML sites, no anti-bot. Uses httpx directly (no proxy needed).
Web Unlocker would work but is a waste of money for these sites.
"""
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone

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

SITES = {
    "jobs_ie": {
        "search_url": "https://www.jobs.ie/SearchResults.aspx?Keywords={query}&Location=0&Category=&Recruiter=",
        "card_patterns": [r'class="[^"]*job-result[^"]*"', r'class="[^"]*listing[^"]*"'],
        "title_pattern": r'<a[^>]*class="[^"]*job-title[^"]*"[^>]*>([^<]+)</a>',
        "company_pattern": r'<span[^>]*class="[^"]*company[^"]*"[^>]*>([^<]+)</span>',
        "location_pattern": r'<span[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</span>',
        "link_pattern": r'<a[^>]*class="[^"]*job-title[^"]*"[^>]*href="([^"]+)"',
    },
    "irishjobs": {
        "search_url": "https://www.irishjobs.ie/ShowResults.aspx?Keywords={query}&Location=100&Category=&Recruiter=",
        "card_patterns": [r'class="[^"]*job-result[^"]*"', r'class="[^"]*search-result[^"]*"'],
        "title_pattern": r'<a[^>]*class="[^"]*job-result-title[^"]*"[^>]*>([^<]+)</a>',
        "company_pattern": r'<h3[^>]*class="[^"]*job-result-company[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>',
        "location_pattern": r'<li[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</li>',
        "link_pattern": r'<a[^>]*class="[^"]*job-result-title[^"]*"[^>]*href="([^"]+)"',
    },
    "gradireland": {
        "search_url": "https://gradireland.com/graduate-jobs?keyword={query}",
        "card_patterns": [r'class="[^"]*views-row[^"]*"', r'class="[^"]*job-teaser[^"]*"'],
        "title_pattern": r'<h\d[^>]*class="[^"]*field--name-title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>',
        "company_pattern": r'<div[^>]*class="[^"]*field--name-field-company[^"]*"[^>]*>([^<]+)</div>',
        "location_pattern": r'<div[^>]*class="[^"]*field--name-field-location[^"]*"[^>]*>([^<]+)</div>',
        "link_pattern": r'<h\d[^>]*class="[^"]*field--name-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"',
    },
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
    key = f"{company.lower().strip()}|{title.lower().strip()}|{desc[:500].lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def _scrape_site(site_key, config, queries, client):
    """Scrape one Irish portal. Returns list of job dicts."""
    jobs = []
    for query in queries:
        url = config["search_url"].format(query=query.replace(" ", "+"))
        try:
            resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[{site_key}] HTTP {resp.status_code} for {query}")
                continue

            page = resp.text
            titles = [_clean(m) for m in re.findall(config["title_pattern"], page)]
            companies = [_clean(m) for m in re.findall(config["company_pattern"], page)]
            locations = [_clean(m) for m in re.findall(config["location_pattern"], page)]
            links = re.findall(config["link_pattern"], page)

            for i in range(min(len(titles), 50)):
                title = titles[i] if i < len(titles) else ""
                company = companies[i] if i < len(companies) else ""
                location = locations[i] if i < len(locations) else "Ireland"
                link = links[i] if i < len(links) else ""

                if not title:
                    continue

                # Make absolute URL
                if link and not link.startswith("http"):
                    base = config["search_url"].split("/")[0:3]
                    link = "/".join(base) + link

                # Try to fetch detail page for full description
                description = ""
                desc_quality = "snippet"
                if link:
                    try:
                        detail = client.get(link, headers=HEADERS, timeout=15, follow_redirects=True)
                        if detail.status_code == 200:
                            # Extract main content
                            for selector in [r'class="[^"]*job-description[^"]*"', r'class="[^"]*description[^"]*"', r'<article[^>]*>']:
                                match = re.search(selector + r'(.*?)</(?:div|article|section)>', detail.text, re.DOTALL)
                                if match:
                                    description = _clean(match.group(1) if '>' not in match.group(0)[:50] else match.group(0))
                                    if len(description) > 100:
                                        desc_quality = "full"
                                        break
                    except Exception:
                        pass

                jobs.append({
                    "title": title[:500],
                    "company": (company or site_key)[:200],
                    "description": description[:10000],
                    "location": location[:200],
                    "apply_url": (link or "")[:1000],
                    "source": site_key,
                    "description_quality": desc_quality,
                })

            logger.info(f"[{site_key}] Query '{query}': {len(titles)} jobs found")
        except Exception as e:
            logger.error(f"[{site_key}] Query '{query}' failed: {e}")

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

    for site_key, config in SITES.items():
        site_jobs = _scrape_site(site_key, config, queries, client)
        all_jobs.extend(site_jobs)

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
        # Batch upsert in chunks (Supabase has row limits)
        for i in range(0, len(all_jobs), 50):
            chunk = all_jobs[i:i+50]
            db.table("jobs_raw").upsert(chunk, on_conflict="job_hash").execute()

    logger.info(f"[irish_portals] {len(all_jobs)} total jobs across 3 sites")
    return {"count": len(all_jobs), "source": "irish_portals",
            "new_job_hashes": [j["job_hash"] for j in all_jobs]}
