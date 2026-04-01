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


def _extract_profiles(html_text, role_name, role_type):
    """Extract LinkedIn profiles from Google search results HTML."""
    contacts = []
    # Find LinkedIn profile URLs in Google results
    li_urls = re.findall(r'(https?://\w+\.linkedin\.com/in/[^"&?\s]+)', html_text)
    seen_urls = set()

    for url in li_urls[:3]:
        url = url.rstrip('/')
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Try to extract name from surrounding text
        # Google results typically have "Name - Title - Company | LinkedIn"
        name_match = re.search(
            re.escape(url).replace(r'https', 'https?') + r'[^>]*>([^<]+)',
            html_text
        )
        name = ""
        if name_match:
            text = name_match.group(1).strip()
            # Clean "Name - Title - Company | LinkedIn" → just the name
            name = text.split(" - ")[0].split(" | ")[0].strip()
            if len(name) > 50:
                name = name[:50]

        first_name = name.split()[0] if name else "there"
        contacts.append({
            "name": name,
            "role": role_name,
            "role_type": role_type,
            "why": f"{role_name} at the company — likely involved in hiring",
            "message": f"Hi {first_name}, I came across a role at your company that matches my background in cloud engineering and DevOps. Would love to connect and learn more about the team and culture.",
            "profile_url": url,
            "search_url": f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(name)}" if name else "",
            "google_url": "",
        })

    return contacts


def handler(event, context):
    user_id = event.get("user_id", "")
    max_jobs = event.get("max_jobs", 15)

    if not user_id:
        return {"count": 0, "error": "no user_id"}

    db = get_supabase()
    proxy_url = get_param("/naukribaba/PROXY_URL")

    # Get matched jobs that need contacts
    result = db.table("jobs").select("job_id, title, company") \
        .eq("user_id", user_id) \
        .not_.is_("resume_s3_url", "null") \
        .is_("linkedin_contacts", "null") \
        .order("match_score", desc=True) \
        .limit(max_jobs) \
        .execute()

    jobs = result.data or []
    if not jobs:
        logger.info("[contacts] No jobs need contacts")
        return {"count": 0, "source": "contacts"}

    logger.info(f"[contacts] Finding contacts for {len(jobs)} jobs")
    updated = 0

    for job in jobs:
        company = job["company"]
        all_contacts = []

        for role_name, role_type in SEARCH_ROLES:
            query = f'site:linkedin.com/in "{company}" "{role_name}"'
            url = f"https://www.google.com/search?q={quote_plus(query)}&num=3"

            try:
                resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
                if resp.status_code == 200:
                    profiles = _extract_profiles(resp.text, role_name, role_type)
                    all_contacts.extend(profiles)
            except Exception as e:
                logger.warning(f"[contacts] Search failed for {company} {role_name}: {e}")

        if all_contacts:
            try:
                db.table("jobs").update({
                    "linkedin_contacts": json.dumps(all_contacts)
                }).eq("job_id", job["job_id"]).execute()
                updated += 1
                logger.info(f"[contacts] {company}: {len(all_contacts)} contacts saved")
            except Exception as e:
                logger.error(f"[contacts] Save failed for {job['job_id']}: {e}")

    logger.info(f"[contacts] Done: {updated}/{len(jobs)} jobs got contacts")
    return {"count": updated, "source": "contacts"}
