"""Glassdoor job scraper using Bright Data Web Unlocker.

Scrapes public Glassdoor job search pages via httpx + Web Unlocker proxy.
Extracts job cards from search results, then fetches full descriptions
from individual detail pages. Stops if login overlay is detected.
"""
import html
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


def _has_login_wall(html_text):
    """Detect if Glassdoor is showing a login overlay."""
    login_indicators = [
        'class="LoginModal"',
        'class="loginModal"',
        'id="LoginModal"',
        'data-test="login-modal"',
        '"hardsellOverlay"',
        'class="locked"',
        'Sign in to view',
    ]
    return any(indicator in html_text for indicator in login_indicators)


def _parse_search_page(html_text):
    """Extract job cards from Glassdoor search results HTML."""
    jobs = []

    if _has_login_wall(html_text):
        logger.warning("[glassdoor] Login overlay detected on search page")
        return jobs

    # Try to extract from embedded JSON (Glassdoor sometimes embeds Apollo state)
    json_match = re.search(
        r'window\.__APOLLO_STATE__\s*=\s*(\{.+?\});\s*$',
        html_text, re.MULTILINE
    )
    if json_match:
        try:
            apollo_data = __import__("json").loads(json_match.group(1))
            for key, val in apollo_data.items():
                if not isinstance(val, dict):
                    continue
                if val.get("__typename") == "JobListingSearchResult" or "jobTitle" in val:
                    title = val.get("jobTitle") or val.get("jobTitleText") or ""
                    employer = val.get("employer") or {}
                    company = employer.get("name") or employer.get("shortName") or ""
                    if isinstance(company, dict):
                        company = company.get("name") or ""
                    location = val.get("locationName") or val.get("location") or ""
                    job_url = val.get("jobUrl") or val.get("seoJobLink") or ""
                    listing_id = val.get("listingId") or val.get("jobListingId") or ""

                    if not title or not company:
                        continue

                    if job_url and not job_url.startswith("http"):
                        job_url = f"https://www.glassdoor.com{job_url}"

                    jobs.append({
                        "title": _clean_html(title),
                        "company": _clean_html(str(company)),
                        "location": _clean_html(str(location)),
                        "apply_url": job_url,
                        "listing_id": str(listing_id),
                        "detail_url": job_url,
                    })
            if jobs:
                logger.info(f"[glassdoor] Extracted {len(jobs)} jobs from Apollo state")
                return jobs
        except Exception as e:
            logger.warning(f"[glassdoor] Apollo state parse failed: {e}")

    # Fallback: parse HTML job cards
    # Glassdoor uses JobCard or job-listing patterns
    # Extract job listing links with IDs
    listing_links = re.findall(
        r'<a[^>]*href="(/partner/jobListing\.htm\?pos=\d+[^"]*|/job-listing/[^"]*)"[^>]*>',
        html_text
    )
    if not listing_links:
        listing_links = re.findall(
            r'<a[^>]*href="(/partner/jobListing[^"]*|/Job/[^"]*\.htm[^"]*)"[^>]*>',
            html_text
        )

    # Job titles from data-test or class patterns
    title_matches = re.findall(
        r'<a[^>]*data-test="job-title"[^>]*>([^<]+)</a>',
        html_text
    )
    if not title_matches:
        title_matches = re.findall(
            r'<a[^>]*class="[^"]*jobTitle[^"]*"[^>]*>([^<]+)</a>',
            html_text
        )

    # Company names
    company_matches = re.findall(
        r'<span[^>]*class="[^"]*EmployerProfile[^"]*"[^>]*>([^<]+)</span>',
        html_text
    )
    if not company_matches:
        company_matches = re.findall(
            r'<div[^>]*data-test="emp-name"[^>]*>([^<]+)</div>',
            html_text
        )
    if not company_matches:
        company_matches = re.findall(
            r'<span[^>]*class="[^"]*employer-name[^"]*"[^>]*>([^<]+)</span>',
            html_text
        )

    # Locations
    location_matches = re.findall(
        r'<span[^>]*data-test="emp-location"[^>]*>([^<]+)</span>',
        html_text
    )
    if not location_matches:
        location_matches = re.findall(
            r'<div[^>]*class="[^"]*location[^"]*"[^>]*>([^<]+)</div>',
            html_text
        )

    for i in range(len(title_matches)):
        title = _clean_html(title_matches[i])
        company = _clean_html(company_matches[i]) if i < len(company_matches) else ""
        location = _clean_html(location_matches[i]) if i < len(location_matches) else ""

        detail_url = ""
        if i < len(listing_links):
            link = listing_links[i]
            detail_url = f"https://www.glassdoor.com{link}" if not link.startswith("http") else link

        if not title or not company:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "apply_url": detail_url,
            "listing_id": "",
            "detail_url": detail_url,
        })

    return jobs


def _fetch_job_detail(detail_url, proxy_url):
    """Fetch full job description from Glassdoor detail page."""
    if not detail_url:
        return None

    try:
        resp = httpx.get(detail_url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
        if resp.status_code != 200:
            return None

        text = resp.text

        # Check for login wall on detail page
        if _has_login_wall(text):
            logger.warning("[glassdoor] Login overlay detected on detail page")
            return "LOGIN_WALL"

        # Try data-test="jobDescriptionContent"
        desc_match = re.search(
            r'<div[^>]*data-test="jobDescriptionContent"[^>]*>(.*?)</div>\s*</div>',
            text, re.DOTALL
        )
        if not desc_match:
            # Try class="jobDescriptionContent"
            desc_match = re.search(
                r'<div[^>]*class="[^"]*jobDescriptionContent[^"]*"[^>]*>(.*?)</div>\s*</div>',
                text, re.DOTALL
            )
        if not desc_match:
            # Try broader pattern
            desc_match = re.search(
                r'<div[^>]*class="[^"]*desc[^"]*"[^>]*>(.*?)</div>',
                text, re.DOTALL
            )

        if desc_match:
            return _clean_html(desc_match.group(1))

    except Exception as e:
        logger.warning(f"[glassdoor] Detail fetch failed for {detail_url}: {e}")
    return None


def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    location = event.get("location", "Ireland")
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)
    max_jobs = event.get("max_jobs", 30)

    db = get_supabase()
    proxy_url = get_param("/naukribaba/PROXY_URL")

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "glassdoor").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "glassdoor", "cached": True}

    from normalizers import normalize_job
    all_jobs = []
    login_wall_hit = False

    for query in queries:
        encoded_query = quote_plus(query)
        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={encoded_query}&locT=N&locId=104"

        try:
            resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
            if resp.status_code != 200:
                logger.warning(f"[glassdoor] Search returned HTTP {resp.status_code}")
                continue

            if _has_login_wall(resp.text):
                logger.warning("[glassdoor] Login wall detected on search page, stopping")
                login_wall_hit = True
                break

            cards = _parse_search_page(resp.text)
            logger.info(f"[glassdoor] Query '{query}': {len(cards)} cards found")

            for card in cards[:max_jobs - len(all_jobs)]:
                # Fetch full description from detail page
                full_desc = None
                if card.get("detail_url"):
                    full_desc = _fetch_job_detail(card["detail_url"], proxy_url)

                    # Stop scraping if login wall is hit on detail pages
                    if full_desc == "LOGIN_WALL":
                        logger.warning("[glassdoor] Login wall hit on detail page, stopping detail fetches")
                        login_wall_hit = True
                        full_desc = None

                desc_quality = "full" if full_desc else "snippet"
                description = full_desc or card.get("title", "")

                job = normalize_job({
                    "title": card["title"],
                    "company": card["company"],
                    "description": description,
                    "location": card["location"],
                    "url": card["apply_url"],
                }, source="glassdoor", query_hash=query_hash)

                if job:
                    job["description_quality"] = desc_quality
                    all_jobs.append(job)

                if len(all_jobs) >= max_jobs:
                    break

                # If login wall was hit, stop fetching details but keep cards we have
                if login_wall_hit:
                    # Add remaining cards without detail fetching
                    remaining = cards[cards.index(card) + 1:max_jobs - len(all_jobs)]
                    for rem_card in remaining:
                        rem_job = normalize_job({
                            "title": rem_card["title"],
                            "company": rem_card["company"],
                            "description": rem_card.get("title", ""),
                            "location": rem_card["location"],
                            "url": rem_card["apply_url"],
                        }, source="glassdoor", query_hash=query_hash)
                        if rem_job:
                            rem_job["description_quality"] = "snippet"
                            all_jobs.append(rem_job)
                        if len(all_jobs) >= max_jobs:
                            break
                    break

        except Exception as e:
            logger.error(f"[glassdoor] Query '{query}' failed: {e}")

        if len(all_jobs) >= max_jobs or login_wall_hit:
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

    logger.info(f"[glassdoor] {len(all_jobs)} jobs saved")
    return {"count": len(all_jobs), "source": "glassdoor",
            "new_job_hashes": [j["job_hash"] for j in all_jobs]}
