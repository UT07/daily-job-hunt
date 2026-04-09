"""Find LinkedIn contacts for a job using Apify Google Search Scraper.

Searches Google for LinkedIn profiles of hiring managers, recruiters,
and team leads at the company. Uses Apify for structured search results
(better name/title extraction than raw HTML scraping).

Cost: ~$0.008 per job (3 searches × ~5 results each).
"""
import json
import logging

import boto3

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


def _extract_contacts_from_results(results: list, role_name: str, role_type: str, company: str) -> list:
    """Extract LinkedIn contacts from Apify Google Search results."""
    contacts = []
    seen_urls = set()

    for r in results:
        organic = r.get("organicResults", [])
        for item in organic[:5]:
            url = item.get("url", "")
            if "linkedin.com/in/" not in url:
                continue

            # Clean URL
            url = url.split("?")[0].split("#")[0].rstrip("/")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract name from Google result title: "Name - Title - Company | LinkedIn"
            title_text = item.get("title", "")
            name = title_text.split(" - ")[0].split(" | ")[0].strip()
            if len(name) > 50 or len(name) < 2:
                name = ""

            # Extract headline from description
            headline = (item.get("description") or "")[:200]

            first_name = name.split()[0] if name else "there"
            contacts.append({
                "name": name,
                "role": role_name,
                "role_type": role_type,
                "headline": headline,
                "why": f"{role_name} at {company} — likely involved in hiring for this role",
                "message": (
                    f"Hi {first_name}, I noticed a role at {company} that aligns "
                    f"well with my background in cloud infrastructure and backend "
                    f"engineering. I would appreciate the chance to connect and "
                    f"learn more about the team."
                ),
                "profile_url": url,
            })

    return contacts[:3]  # Max 3 per role


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
        apify_key = get_param("/naukribaba/APIFY_API_KEY")
        if not apify_key or apify_key.startswith("mock"):
            logger.info(f"[contacts] No Apify key, skipping {job_hash}")
            return {"job_hash": job_hash, "user_id": user_id, "contacts_found": 0, "skipped": "no_apify_key"}
    except Exception:
        return {"job_hash": job_hash, "user_id": user_id, "contacts_found": 0, "skipped": "no_apify_key"}

    from apify_client import ApifyClient
    client = ApifyClient(apify_key)

    company = job["company"]
    location = job.get("location", "")
    location_hint = location.split(",")[0].strip() if location else "Dublin"

    all_contacts = []
    for role_name, role_type in SEARCH_ROLES:
        query = f'site:linkedin.com/in "{company}" "{role_name}" "{location_hint}"'
        try:
            run = client.actor("apify/google-search-scraper").call(
                run_input={
                    "queries": query,
                    "maxPagesPerQuery": 1,
                    "resultsPerPage": 5,
                },
                timeout_secs=30,
            )
            results = client.dataset(run["defaultDatasetId"]).list_items().items
            contacts = _extract_contacts_from_results(results, role_name, role_type, company)
            all_contacts.extend(contacts)
        except Exception as e:
            logger.warning(f"[contacts] Apify search failed for {company} {role_name}: {e}")

    # Deduplicate by URL
    seen = set()
    deduped = []
    for c in all_contacts:
        if c["profile_url"] not in seen:
            seen.add(c["profile_url"])
            deduped.append(c)

    if deduped:
        db.table("jobs").update({"linkedin_contacts": json.dumps(deduped)}) \
            .eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[contacts] {company}: {len(deduped)} contacts found via Apify")
    return {"job_hash": job_hash, "user_id": user_id, "contacts_found": len(deduped)}
