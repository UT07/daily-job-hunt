"""Find LinkedIn contacts for a job using Bright Data Web Unlocker.

Uses the same extraction logic as scrapers/scrape_contacts.py.
Called per-job from the single-job pipeline or dashboard "Find Contacts" button.
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
    url = re.sub(r'/overlay/.*$', '', url)
    return url


def _extract_name_from_slug(url: str) -> str:
    """Extract a human name from a LinkedIn slug like /in/john-doe-123abc."""
    slug = url.rstrip("/").rsplit("/in/", 1)[-1] if "/in/" in url else ""
    if not slug:
        return ""
    slug = re.sub(r'-[a-f0-9]{6,}$', '', slug)
    name = slug.replace("-", " ").title()
    if len(name) < 3 or len(name) > 40 or any(c in name for c in '<>{}()[]="'):
        return ""
    return name


def _extract_profiles(html_text: str, role_name: str, role_type: str, company: str) -> list:
    """Extract LinkedIn profiles from Google search results HTML."""
    contacts = []
    seen_urls = set()

    raw_urls = re.findall(r'https?://\w+\.linkedin\.com/in/[a-zA-Z0-9_-]+', html_text)
    title_matches = re.findall(r'<h3[^>]*>([^<]+)</h3>', html_text)

    title_names = {}
    for title in title_matches:
        if "linkedin" in title.lower():
            parts = title.split(" - ")
            if parts:
                candidate_name = parts[0].split(" | ")[0].split(" — ")[0].strip()
                if 2 < len(candidate_name) < 40 and not any(c in candidate_name for c in '<>{}()[]="0123456789'):
                    title_names[candidate_name] = True

    for url in raw_urls:
        clean = _clean_url(url)
        if clean in seen_urls or "/in/" not in clean:
            continue
        seen_urls.add(clean)

        name = ""
        slug_name = _extract_name_from_slug(clean)
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
    job_hash = event["job_hash"]
    user_id = event["user_id"]

    db = get_supabase()

    job = db.table("jobs").select("company, title, location").eq("user_id", user_id) \
        .eq("job_hash", job_hash).execute()
    if not job.data:
        return {"job_hash": job_hash, "contacts_found": 0}
    job = job.data[0]

    try:
        proxy_url = get_param("/naukribaba/PROXY_URL")
    except Exception:
        logger.info(f"[contacts] No proxy configured, skipping {job_hash}")
        return {"job_hash": job_hash, "user_id": user_id, "contacts_found": 0, "skipped": "no_proxy"}

    company = job["company"]
    location = job.get("location", "")
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

    # Deduplicate
    seen = set()
    deduped = []
    for c in all_contacts:
        if c["profile_url"] not in seen:
            seen.add(c["profile_url"])
            deduped.append(c)

    if deduped:
        db.table("jobs").update({"linkedin_contacts": json.dumps(deduped)}) \
            .eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[contacts] {company}: {len(deduped)} contacts found")
    return {"job_hash": job_hash, "user_id": user_id, "contacts_found": len(deduped)}
