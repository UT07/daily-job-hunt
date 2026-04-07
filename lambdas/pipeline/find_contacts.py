"""Find LinkedIn contacts for a job using Bright Data Web Unlocker.

Searches Google for LinkedIn profiles of hiring managers, recruiters,
and team leads at the company. Uses the same Web Unlocker proxy as scrapers.
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


def _extract_profiles(html_text, role_name, role_type):
    """Extract LinkedIn profiles from Google search results HTML."""
    contacts = []
    li_urls = re.findall(r'(https?://\w+\.linkedin\.com/in/[^"&?\s]+)', html_text)
    seen_urls = set()

    for url in li_urls[:3]:
        url = url.rstrip('/')
        if url in seen_urls:
            continue
        seen_urls.add(url)

        name_match = re.search(
            re.escape(url).replace(r'https', 'https?') + r'[^>]*>([^<]+)',
            html_text
        )
        name = ""
        if name_match:
            text = name_match.group(1).strip()
            name = text.split(" - ")[0].split(" | ")[0].strip()
            if len(name) > 50:
                name = name[:50]

        first_name = name.split()[0] if name else "there"
        contacts.append({
            "name": name,
            "role": role_name,
            "role_type": role_type,
            "why": f"{role_name} at the company — likely involved in hiring",
            "message": (
                f"Hi {first_name}, I came across a role at your company that matches "
                f"my background in cloud engineering and DevOps. Would love to connect "
                f"and learn more about the team and culture."
            ),
            "profile_url": url,
        })

    return contacts


def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]

    db = get_supabase()

    job = db.table("jobs").select("company, title").eq("user_id", user_id) \
        .eq("job_hash", job_hash).execute()
    if not job.data:
        return {"job_hash": job_hash, "contacts": [], "contacts_found": 0}
    job = job.data[0]

    # Get Bright Data proxy
    try:
        proxy_url = get_param("/naukribaba/PROXY_URL")
    except Exception:
        logger.info(f"[contacts] No proxy configured, skipping {job_hash}")
        return {"job_hash": job_hash, "user_id": user_id, "contacts_found": 0, "skipped": "no_proxy"}

    company = job["company"]
    all_contacts = []

    for role_name, role_type in SEARCH_ROLES:
        query = f'site:linkedin.com/in "{company}" "{role_name}"'
        url = f"https://www.google.com/search?q={quote_plus(query)}&num=3"

        try:
            resp = httpx.get(url, proxy=proxy_url, timeout=20, follow_redirects=True, verify=False)
            if resp.status_code == 200:
                profiles = _extract_profiles(resp.text, role_name, role_type)
                all_contacts.extend(profiles)
        except Exception as e:
            logger.warning(f"[contacts] Search failed for {company} {role_name}: {e}")

    if all_contacts:
        db.table("jobs").update({"linkedin_contacts": json.dumps(all_contacts)}) \
            .eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[contacts] {company}: {len(all_contacts)} contacts found")
    return {"job_hash": job_hash, "user_id": user_id, "contacts_found": len(all_contacts)}
