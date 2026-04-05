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
    """Extract job description from a StepStone detail page HTML."""
    detail_clean = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL)
    for pattern in [
        r'data-testid="vacancy-description"[^>]*>(.*?)</div>\s*</div>',
        r'data-testid="job-description"[^>]*>(.*?)</div>\s*</div>',
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
    """Scrape GradIreland (Drupal/views-based).

    Uses multiple extraction strategies to stay resilient across Drupal
    template changes:
      1. JSON-LD structured data (most reliable when present)
      2. Drupal views-row / field--name-title pattern (original approach)
      3. Drupal views-row with nested <a> tags (alternate markup)
      4. Article / card-based extraction
      5. Generic job-link href extraction (last resort)

    Also tries alternate URL paths if the primary one returns 0 jobs.
    """
    import json as _json

    _BASE = "https://gradireland.com"
    # URL path patterns to try — GradIreland has changed paths before
    _PATHS = [
        ("/graduate-jobs", "keyword"),
        ("/jobs", "keywords"),
        ("/jobs", "keyword"),
        ("/careers", "keyword"),
        ("/graduate-jobs", "search_api_fulltext"),
    ]

    # Non-job link text to filter out
    _SKIP = {
        "sign in", "register", "cookie", "privacy", "terms",
        "about", "contact", "help", "faq", "browse", "search",
        "all jobs", "view all", "read more", "learn more",
        "log in", "create account", "home", "menu",
    }

    def _is_skip(title):
        lower = title.lower().strip()
        return lower in _SKIP or any(s in lower for s in _SKIP)

    def _company_from_url(url):
        """Infer company name from GradIreland URL slug."""
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path.rstrip("/")
            parts = [p for p in path.split("/") if p]
            prefixes = {"graduate-jobs", "job", "jobs", "vacancy", "opportunities", "careers"}
            if len(parts) >= 2 and parts[0] in prefixes:
                slug = parts[1]
                if not slug.isdigit() and len(slug) >= 2:
                    name = re.sub(r"[-_]+", " ", slug).strip()
                    job_words = {"engineer", "developer", "analyst", "manager", "graduate",
                                 "intern", "trainee", "associate", "officer", "specialist"}
                    if not any(w in name.lower().split() for w in job_words):
                        return name.title()
        except Exception:
            pass
        return ""

    # --- Strategy 1: JSON-LD structured data ---
    def _parse_json_ld(page):
        found = []
        blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            page, re.DOTALL,
        )
        for block in blocks:
            try:
                data = _json.loads(block)
                if isinstance(data, dict) and "@graph" in data:
                    items = data["@graph"]
                elif isinstance(data, list):
                    items = data
                else:
                    items = [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("@type") not in ("JobPosting", "jobPosting"):
                        continue
                    title = (item.get("title") or item.get("name", "")).strip()
                    if not title or len(title) < 3:
                        continue
                    company = ""
                    org = item.get("hiringOrganization", {})
                    if isinstance(org, dict):
                        company = org.get("name", "")
                    elif isinstance(org, str):
                        company = org
                    loc = ""
                    loc_obj = item.get("jobLocation", {})
                    if isinstance(loc_obj, dict):
                        addr = loc_obj.get("address", {})
                        if isinstance(addr, dict):
                            loc = addr.get("addressLocality", "")
                    elif isinstance(loc_obj, list) and loc_obj:
                        first = loc_obj[0]
                        if isinstance(first, dict):
                            addr = first.get("address", {})
                            if isinstance(addr, dict):
                                loc = addr.get("addressLocality", "")
                    apply_url = item.get("url", "") or item.get("sameAs", "")
                    desc = _clean(item.get("description", "") or "")
                    found.append({
                        "title": title[:500],
                        "company": (company or "")[:200],
                        "description": desc[:2000],
                        "location": (loc or "Ireland")[:200],
                        "apply_url": apply_url[:1000],
                        "source": "gradireland",
                    })
            except (ValueError, KeyError, TypeError):
                continue
        return found

    # --- Strategy 2: Drupal field--name-title pattern (original) ---
    def _parse_drupal_fields(page):
        found = []
        titles = [_clean(m) for m in re.findall(
            r'<h\d[^>]*class="[^"]*field--name-title[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>', page
        )]
        companies = [_clean(m) for m in re.findall(
            r'<div[^>]*class="[^"]*field--name-field-company[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)', page
        )]
        locations = [_clean(m) for m in re.findall(
            r'<div[^>]*class="[^"]*field--name-field-location[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)', page
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
                link = f"{_BASE}{link}"
            if not company:
                company = _company_from_url(link)
            found.append({
                "title": title[:500],
                "company": (company or "")[:200],
                "description": "",
                "location": location[:200],
                "apply_url": link[:1000],
                "source": "gradireland",
            })
        return found

    # --- Strategy 3: Drupal views-row pattern ---
    def _parse_views_rows(page):
        found = []
        rows = re.split(r'<div[^>]*class="[^"]*views-row[^"]*"', page)
        if len(rows) <= 1:
            return []
        for row_html in rows[1:]:
            title_match = re.search(
                r'class="[^"]*(?:views-field-title|field--name-title|views-field-name)[^"]*"'
                r'[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>\s*([^<]+)',
                row_html, re.DOTALL,
            )
            if not title_match:
                title_match = re.search(
                    r'<a[^>]+href="(/[^"]*(?:job|graduate|career|vacanc)[^"]*)"[^>]*>\s*([^<]{5,120})',
                    row_html, re.DOTALL,
                )
            if not title_match:
                continue
            href = title_match.group(1).strip()
            title = _clean(title_match.group(2))
            if not title or len(title) < 5 or _is_skip(title):
                continue
            link = f"{_BASE}{href}" if not href.startswith("http") else href
            company = ""
            for pat in [
                r'class="[^"]*(?:field--name-field-company|views-field-field-company|company)[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
                r'class="[^"]*employer[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
            ]:
                m = re.search(pat, row_html, re.DOTALL | re.IGNORECASE)
                if m:
                    company = _clean(m.group(1))
                    break
            if not company:
                company = _company_from_url(link)
            location = "Ireland"
            for pat in [
                r'class="[^"]*(?:field--name-field-location|views-field-field-location|location)[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
            ]:
                m = re.search(pat, row_html, re.DOTALL | re.IGNORECASE)
                if m:
                    location = _clean(m.group(1)) or "Ireland"
                    break
            found.append({
                "title": title[:500],
                "company": (company or "")[:200],
                "description": "",
                "location": location[:200],
                "apply_url": link[:1000],
                "source": "gradireland",
            })
        return found

    # --- Strategy 4: Article / card-based extraction ---
    def _parse_article_cards(page):
        found = []
        articles = re.split(
            r'<(?:article|div)[^>]*class="[^"]*(?:job-card|job-item|search-result|node--type-job|job-teaser|job-listing)[^"]*"',
            page, flags=re.IGNORECASE,
        )
        if len(articles) <= 1:
            return []
        for art in articles[1:]:
            end = re.search(r'</article>|<article', art)
            if end:
                art = art[:end.start()]
            title_m = re.search(
                r'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]{5,120})',
                art, re.DOTALL,
            )
            if not title_m:
                continue
            href = title_m.group(1).strip()
            title = _clean(title_m.group(2))
            if not title or len(title) < 5 or _is_skip(title):
                continue
            link = f"{_BASE}{href}" if not href.startswith("http") else href
            company = _company_from_url(link)
            found.append({
                "title": title[:500],
                "company": (company or "")[:200],
                "description": "",
                "location": "Ireland",
                "apply_url": link[:1000],
                "source": "gradireland",
            })
        return found

    # --- Strategy 5: Generic job-link href extraction ---
    def _parse_generic_links(page):
        found = []
        seen = set()
        pattern = re.compile(
            r'<a[^>]+href="((?:https?://(?:www\.)?gradireland\.com)?/[^"]*'
            r'(?:job|vacanc|opportunit|career|graduate)[^"]*)"[^>]*>\s*([^<]{5,120}?)\s*</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern.finditer(page):
            url = m.group(1).strip()
            if url in seen:
                continue
            seen.add(url)
            title = _clean(m.group(2))
            if not title or len(title) < 5 or _is_skip(title):
                continue
            if not url.startswith("http"):
                url = f"{_BASE}{url}"
            found.append({
                "title": title[:500],
                "company": _company_from_url(url)[:200],
                "description": "",
                "location": "Ireland",
                "apply_url": url[:1000],
                "source": "gradireland",
            })
        return found

    # --- Main scraping loop ---
    jobs = []
    for query in queries:
        query_jobs = []

        # Try each URL path pattern until one yields results
        for path, param_name in _PATHS:
            encoded = query.replace(' ', '+')
            url = f"{_BASE}{path}?{param_name}={encoded}"
            try:
                resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
                if resp.status_code in (404, 403):
                    logger.debug(f"[gradireland] HTTP {resp.status_code} for path {path}")
                    continue  # try next path
                if resp.status_code != 200:
                    logger.warning(f"[gradireland] HTTP {resp.status_code} for {query}")
                    continue

                page = resp.text

                # Detect bot protection
                if "captcha" in page.lower() or "access denied" in page.lower()[:500]:
                    logger.warning("[gradireland] Blocked by bot protection")
                    break

                # Try each strategy in order of reliability
                for strategy_name, strategy_fn in [
                    ("json_ld", _parse_json_ld),
                    ("drupal_fields", _parse_drupal_fields),
                    ("views_rows", _parse_views_rows),
                    ("article_cards", _parse_article_cards),
                    ("generic_links", _parse_generic_links),
                ]:
                    query_jobs = strategy_fn(page)
                    if query_jobs:
                        logger.info(
                            f"[gradireland] Query '{query}': {len(query_jobs)} jobs "
                            f"via {strategy_name} strategy (path={path})"
                        )
                        break

                if query_jobs:
                    break  # found jobs on this path, stop trying others

            except Exception as e:
                logger.error(f"[gradireland] Query '{query}' failed on path {path}: {e}")

        if not query_jobs:
            logger.warning(
                f"[gradireland] 0 jobs for '{query}' across all URL paths and strategies "
                "-- template may have changed, check HTML selectors"
            )

        jobs.extend(query_jobs)

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
