"""LinkedIn contacts finder using Bright Data Web Unlocker.

Searches Google for LinkedIn profiles of hiring managers, recruiters,
and team leads at companies with matched jobs. Uses httpx + Web Unlocker
proxy to bypass Google's rate limiting.

Runs AFTER scoring — only finds contacts for matched jobs (10-15/day).
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
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


# A job whose searches ran and found nobody is not searched again for this
# long. Without a stamp the select below (linkedin_contacts IS NULL, top N by
# score) returned the same N jobs on every run, re-searched them through the
# paid proxy, found nothing again, and never reached job N+1.
RETRY_AFTER_DAYS = 7

# Column added by supabase/migrations/20261008120000_jobs_contacts_attempted_at.sql.
_ATTEMPT_COL = "contacts_attempted_at"


def _missing_attempt_column(exc: Exception) -> bool:
    """True if exc is PostgREST/Postgres saying the stamp column is absent.

    PostgREST reports a missing column as PGRST204 "...schema cache", never
    "does not exist", so match on the column name rather than the wording.
    """
    return _ATTEMPT_COL in str(exc)


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
    retry_after_days = event.get("retry_after_days", RETRY_AFTER_DAYS)

    if not user_id:
        return {"count": 0, "error": "no user_id"}

    db = get_supabase()
    proxy_url = get_param("/naukribaba/PROXY_URL")

    # Matched jobs that need contacts (S+A, none yet), skipping any searched
    # inside the retry window so the run moves down the list.
    # 'Z' rather than isoformat()'s "+00:00": a literal '+' inside a query
    # string can arrive as a space, and the filter would then fail to parse.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retry_after_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _select(with_stamp: bool):
        q = db.table("jobs").select("job_id, title, company, location, score_tier") \
            .eq("user_id", user_id) \
            .in_("score_tier", ["S", "A"]) \
            .is_("linkedin_contacts", "null")
        if with_stamp:
            q = q.or_(f"{_ATTEMPT_COL}.is.null,{_ATTEMPT_COL}.lt.{cutoff}")
        return q.order("match_score", desc=True).limit(max_jobs).execute()

    # Deployed before the migration is applied, the stamp column does not
    # exist. Fall back to the legacy select so contacts still get found, but
    # say so in the result: this run cannot make progress past jobs with no
    # findable contacts.
    stamp_ok = True
    try:
        result = _select(with_stamp=True)
    except Exception as e:
        if not _missing_attempt_column(e):
            raise
        stamp_ok = False
        logger.error(f"[contacts] {_ATTEMPT_COL} missing ({e}); apply the migration. "
                     f"Falling back to the legacy select, which re-searches the same jobs.")
        result = _select(with_stamp=False)

    def _done(payload):
        if not stamp_ok:
            payload["error"] = (f"migration_missing: jobs.{_ATTEMPT_COL} does not exist, so "
                                f"jobs without contacts are re-searched every run")
        return payload

    jobs = result.data or []
    if not jobs:
        logger.info("[contacts] No S+A jobs need contacts")
        return _done({"count": 0, "attempted": 0, "source": "contacts"})

    logger.info(f"[contacts] Finding contacts for {len(jobs)} S+A jobs")
    updated = 0
    attempted = 0

    for job in jobs:
        company = job["company"]
        location = job.get("location", "")
        # Extract city/region for location-filtered search (e.g. "Dublin" from "Dublin, Ireland")
        location_hint = location.split(",")[0].strip() if location else "Ireland"
        all_contacts = []
        searched = 0  # searches that returned a page we could read

        for role_name, role_type in SEARCH_ROLES:
            query = f'site:linkedin.com/in "{company}" "{role_name}" "{location_hint}"'
            url = f"https://www.google.com/search?q={quote_plus(query)}&num=5"

            try:
                resp = httpx.get(url, proxy=proxy_url, timeout=20, follow_redirects=True, verify=False)
                if resp.status_code == 200:
                    searched += 1
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

        # Stamp only if a search actually ran. If every request failed (proxy
        # down) nothing was looked at, and stamping would push the job back a
        # whole retry window for an outage. An empty result is NOT written to
        # linkedin_contacts: "searched, nobody found" must not read as a
        # contact list.
        payload = {}
        if deduped:
            payload["linkedin_contacts"] = json.dumps(deduped)
        if searched and stamp_ok:
            payload[_ATTEMPT_COL] = datetime.now(timezone.utc).isoformat()
        if searched:
            attempted += 1
        if payload:
            try:
                db.table("jobs").update(payload).eq("job_id", job["job_id"]).execute()
                if deduped:
                    updated += 1
                    logger.info(f"[contacts] {company}: {len(deduped)} contacts saved")
                else:
                    logger.info(f"[contacts] {company}: searched, none found; "
                                f"not retried for {retry_after_days}d")
            except Exception as e:
                logger.error(f"[contacts] Save failed for {job['job_id']}: {e}")

    logger.info(f"[contacts] Done: {updated}/{len(jobs)} jobs got contacts, "
                f"{attempted}/{len(jobs)} searched")
    return _done({"count": updated, "attempted": attempted, "source": "contacts"})
