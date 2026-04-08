"""LinkedIn contacts finder using Bright Data Web Unlocker.

Searches Google for LinkedIn profiles of hiring managers, recruiters,
and team leads at companies with matched jobs. Uses httpx + Web Unlocker
proxy to bypass Google's rate limiting.

Runs AFTER scoring — only finds contacts for matched jobs (10-15/day).
"""
import json
import logging
import re
from urllib.parse import quote_plus

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

SEARCH_ROLES = [
    ("Engineering Manager", "hiring_manager"),
    ("Technical Recruiter", "recruiter"),
    ("Senior Software Engineer", "team_member"),
]


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _clean_url(url: str) -> str:
    """Strip URL fragments, query params, and trailing slashes."""
    url = url.split("#")[0].split("?")[0].rstrip("/")
    # Remove common tracking suffixes
    url = re.sub(r'/overlay/.*$', '', url)
    return url


def _extract_name_from_slug(url: str) -> str:
    """Extract a human name from a LinkedIn slug like /in/john-doe-123abc."""
    slug = url.rstrip("/").rsplit("/in/", 1)[-1] if "/in/" in url else ""
    if not slug:
        return ""
    # Remove trailing hash (e.g. john-doe-a1b2c3d)
    slug = re.sub(r'-[a-f0-9]{6,}$', '', slug)
    # Convert hyphens to spaces, title case
    name = slug.replace("-", " ").title()
    # Filter out garbage
    if len(name) < 3 or len(name) > 40 or any(c in name for c in '<>{}()[]="'):
        return ""
    return name


def _extract_profiles(html_text: str, role_name: str, role_type: str, company: str) -> list:
    """Extract LinkedIn profiles from Google search results HTML."""
    contacts = []
    seen_urls = set()

    # Strategy 1: Find all linkedin.com/in/ URLs
    raw_urls = re.findall(r'https?://\w+\.linkedin\.com/in/[a-zA-Z0-9_-]+', html_text)

    # Strategy 2: Extract titles from Google result <h3> tags near LinkedIn URLs
    # Google wraps results in <h3> with format: "Name - Title - Company | LinkedIn"
    title_matches = re.findall(
        r'<h3[^>]*>([^<]+)</h3>',
        html_text
    )

    # Build a name lookup from titles
    title_names = {}
    for title in title_matches:
        if "linkedin" in title.lower():
            # "John Doe - Engineering Manager - Company | LinkedIn"
            parts = title.split(" - ")
            if parts:
                candidate_name = parts[0].strip()
                # Clean common suffixes
                candidate_name = candidate_name.split(" | ")[0].strip()
                candidate_name = candidate_name.split(" — ")[0].strip()
                if 2 < len(candidate_name) < 40 and not any(c in candidate_name for c in '<>{}()[]="0123456789'):
                    # Try to match this title to a URL found nearby
                    title_names[candidate_name] = True

    for url in raw_urls:
        clean = _clean_url(url)
        if clean in seen_urls:
            continue
        # Skip non-profile URLs
        if "/in/" not in clean:
            continue
        seen_urls.add(clean)

        # Try to get name: first from title matches, then from slug
        name = ""
        slug_name = _extract_name_from_slug(clean)

        # Check if any title name is a reasonable match
        for tname in title_names:
            if slug_name and slug_name.lower().replace(" ", "") in tname.lower().replace(" ", ""):
                name = tname
                break
        if not name:
            name = slug_name

        first_name = name.split()[0] if name else "there"

        contacts.append({
            "name": name,
            "role": role_name,
            "role_type": role_type,
            "why": f"{role_name} at {company} — likely involved in hiring for this role",
            "message": (
                f"Hi {first_name}, I noticed a {role_name.lower()} role at {company} that aligns "
                f"well with my background in cloud infrastructure and backend engineering. "
                f"I would appreciate the chance to connect and learn more about the team."
            ),
            "profile_url": clean,
        })

        if len(contacts) >= 3:
            break

    return contacts


def handler(event, context):
    user_id = event.get("user_id", "")
    max_jobs = event.get("max_jobs", 15)

    if not user_id:
        return {"count": 0, "error": "no user_id"}

    db = get_supabase()
    proxy_url = get_param("/naukribaba/PROXY_URL")

    # Get matched jobs that need contacts (S+A tier, no contacts yet)
    result = db.table("jobs").select("job_id, title, company, location, score_tier") \
        .eq("user_id", user_id) \
        .in_("score_tier", ["S", "A"]) \
        .is_("linkedin_contacts", "null") \
        .order("match_score", desc=True) \
        .limit(max_jobs) \
        .execute()

    jobs = result.data or []
    if not jobs:
        logger.info("[contacts] No S+A jobs need contacts")
        return {"count": 0, "source": "contacts"}

    logger.info(f"[contacts] Finding contacts for {len(jobs)} S+A jobs")
    updated = 0

    for job in jobs:
        company = job["company"]
        location = job.get("location", "")
        # Extract city/region for location-filtered search (e.g. "Dublin" from "Dublin, Ireland")
        location_hint = location.split(",")[0].strip() if location else "Ireland"
        all_contacts = []

        for role_name, role_type in SEARCH_ROLES:
            query = f'site:linkedin.com/in "{company}" "{role_name}" "{location_hint}"'
            url = f"https://www.google.com/search?q={quote_plus(query)}&num=5"

            try:
                resp = httpx.get(url, proxy=proxy_url, timeout=20, follow_redirects=True, verify=False)
                if resp.status_code == 200:
                    profiles = _extract_profiles(resp.text, role_name, role_type, company)
                    all_contacts.extend(profiles)
            except Exception as e:
                logger.warning(f"[contacts] Search failed for {company} {role_name}: {e}")

        # Deduplicate by URL
        seen = set()
        deduped = []
        for c in all_contacts:
            if c["profile_url"] not in seen:
                seen.add(c["profile_url"])
                deduped.append(c)

        if deduped:
            try:
                db.table("jobs").update({
                    "linkedin_contacts": json.dumps(deduped)
                }).eq("job_id", job["job_id"]).execute()
                updated += 1
                logger.info(f"[contacts] {company}: {len(deduped)} contacts saved")
            except Exception as e:
                logger.error(f"[contacts] Save failed for {job['job_id']}: {e}")

    logger.info(f"[contacts] Done: {updated}/{len(jobs)} jobs got contacts")
    return {"count": updated, "source": "contacts"}
