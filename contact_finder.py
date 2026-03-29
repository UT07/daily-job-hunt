"""LinkedIn contact finder — finds REAL profile URLs via Apify actors.

For each matched job, uses Apify to search for actual LinkedIn profiles of
hiring managers, recruiters, and team members at the target company.
Returns real linkedin.com/in/ URLs with names and titles.

Strategies (in order of preference):
  1. Apify Google Search actor — searches Google for LinkedIn profiles (no cookies needed)
  2. Serper.dev fallback — Google Search API (2,500 free queries/month)
  3. Manual Google search URLs — always available as last resort

Set APIFY_API_KEY in .env for Apify. Set SERPER_API_KEY for fallback.
"""

from __future__ import annotations
import json
import logging
import os
import re
import urllib.parse
from typing import List, Dict

from scrapers.base import Job
from ai_client import AIClient
from matcher import extract_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Apify-based LinkedIn profile search
# ---------------------------------------------------------------------------

# Actor IDs — Google Search is preferred (no LinkedIn cookies needed)
APIFY_GOOGLE_SEARCH_ACTOR = "apify/google-search-scraper"
APIFY_LINKEDIN_SEARCH_ACTOR = "curious_coder/linkedin-people-search-scraper"

# Timeouts and limits
APIFY_RUN_TIMEOUT_SECS = 60
APIFY_RUN_MEMORY_MB = 256  # Minimal memory for simple searches


def _get_apify_client():
    """Initialize and return an Apify client, or None if not configured."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        return None
    try:
        from apify_client import ApifyClient
        return ApifyClient(api_key)
    except ImportError:
        logger.warning("[CONTACTS] apify-client package not installed — pip install apify-client")
        return None
    except Exception as e:
        logger.warning(f"[CONTACTS] Failed to initialize Apify client: {e}")
        return None


def _apify_google_search(query: str, num_results: int = 3) -> List[Dict[str, str]]:
    """Search Google via Apify's Google Search Scraper actor.

    Uses the 'apify/google-search-scraper' actor to run Google searches.
    This is the preferred method since it doesn't require LinkedIn cookies
    and reliably finds LinkedIn profile URLs via site:linkedin.com searches.

    Returns list of {name, url, title} for LinkedIn profiles found.
    """
    client = _get_apify_client()
    if client is None:
        return []

    try:
        run_input = {
            "queries": query,
            "maxPagesPerQuery": 1,
            "resultsPerPage": num_results,
            "languageCode": "en",
            "mobileResults": False,
            "includeUnfilteredResults": False,
        }

        logger.debug(f"[CONTACTS] Running Apify Google Search: {query}")
        run = client.actor(APIFY_GOOGLE_SEARCH_ACTOR).call(
            run_input=run_input,
            timeout_secs=APIFY_RUN_TIMEOUT_SECS,
            memory_mbytes=APIFY_RUN_MEMORY_MB,
        )

        if not run or run.get("status") != "SUCCEEDED":
            status = run.get("status", "UNKNOWN") if run else "NO_RUN"
            logger.warning(f"[CONTACTS] Apify Google Search run status: {status}")
            return []

        # Fetch results from the default dataset
        dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items
        profiles = []

        for item in dataset_items:
            # The Google Search actor returns organic results
            organic = item.get("organicResults", [])
            for result in organic:
                link = result.get("url", "")
                title_text = result.get("title", "")

                # Only keep actual LinkedIn profile URLs
                if "/in/" not in link:
                    continue

                name, person_title = _parse_linkedin_title(title_text)
                profiles.append({
                    "name": name,
                    "url": link,
                    "title": person_title,
                })

                if len(profiles) >= num_results:
                    break
            if len(profiles) >= num_results:
                break

        logger.debug(f"[CONTACTS] Apify Google Search found {len(profiles)} profiles")
        return profiles

    except Exception as e:
        logger.warning(f"[CONTACTS] Apify Google Search failed: {e}")
        return []


def _apify_linkedin_people_search(
    company: str, job_title: str, location: str = "", num_results: int = 1
) -> List[Dict[str, str]]:
    """Search LinkedIn people directly via Apify's LinkedIn People Search actor.

    Uses 'curious_coder/linkedin-people-search-scraper'. Requires LinkedIn
    cookies to be set in the actor's input (configured in Apify Console).
    Falls back gracefully if the actor is not available or fails.

    Returns list of {name, url, title} for LinkedIn profiles found.
    """
    client = _get_apify_client()
    if client is None:
        return []

    # Build a LinkedIn people search URL
    search_keywords = f"{job_title} {company}"
    if location:
        search_keywords += f" {location}"

    try:
        run_input = {
            "searchUrls": [
                f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(search_keywords)}"
            ],
            "maxResults": num_results,
            "startPage": 1,
            "minDelay": 2,
            "maxDelay": 5,
        }

        logger.debug(f"[CONTACTS] Running Apify LinkedIn People Search: {search_keywords}")
        run = client.actor(APIFY_LINKEDIN_SEARCH_ACTOR).call(
            run_input=run_input,
            timeout_secs=APIFY_RUN_TIMEOUT_SECS,
            memory_mbytes=APIFY_RUN_MEMORY_MB,
        )

        if not run or run.get("status") != "SUCCEEDED":
            status = run.get("status", "UNKNOWN") if run else "NO_RUN"
            logger.warning(f"[CONTACTS] Apify LinkedIn People Search run status: {status}")
            return []

        dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items
        profiles = []

        for item in dataset_items:
            profile_url = item.get("profileUrl", "") or item.get("url", "")
            full_name = item.get("fullName", "") or item.get("name", "")
            headline = item.get("headline", "") or item.get("title", "")

            if profile_url and "/in/" in profile_url:
                profiles.append({
                    "name": full_name,
                    "url": profile_url,
                    "title": headline,
                })

        logger.debug(f"[CONTACTS] Apify LinkedIn People Search found {len(profiles)} profiles")
        return profiles

    except Exception as e:
        logger.warning(f"[CONTACTS] Apify LinkedIn People Search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Serper.dev fallback (original implementation)
# ---------------------------------------------------------------------------

def _serper_search(query: str, num: int = 3) -> List[Dict[str, str]]:
    """Search Google via Serper.dev API. Returns list of {name, url, title}."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return []

    import requests
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"[CONTACTS] Serper returned {resp.status_code}")
            return []

        profiles = []
        for result in resp.json().get("organic", []):
            link = result.get("link", "")
            title_text = result.get("title", "")

            if "/in/" not in link:
                continue

            name, person_title = _parse_linkedin_title(title_text)
            profiles.append({
                "name": name,
                "url": link,
                "title": person_title,
            })

        return profiles

    except Exception as e:
        logger.warning(f"[CONTACTS] Serper search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Unified search — tries Apify first, then Serper, then gives Google URL
# ---------------------------------------------------------------------------

def _parse_linkedin_title(title_text: str) -> tuple[str, str]:
    """Parse a LinkedIn Google result title into (name, job_title).

    Typical format: 'FirstName LastName - Title at Company | LinkedIn'
    """
    name = ""
    person_title = ""
    parts = re.split(r'\s*[-\u2013\u2014]\s*', title_text)
    if parts:
        name = parts[0].strip().replace(" | LinkedIn", "")
    if len(parts) >= 2:
        person_title = (
            parts[1].strip()
            .replace(" | LinkedIn", "")
            .replace("LinkedIn", "")
            .strip()
        )
    return name, person_title


def _search_linkedin_profile(
    company: str, title: str, location: str = "", num: int = 1
) -> List[Dict[str, str]]:
    """Unified LinkedIn profile search with multi-strategy fallback.

    Order:
      1. Apify Google Search (reliable, no LinkedIn cookies)
      2. Serper.dev (lightweight fallback)

    Returns list of {name, url, title}.
    """
    query = f'site:linkedin.com/in "{title}" "{company}"'
    if location:
        query += f" {location}"

    # Strategy 1: Apify Google Search actor
    profiles = _apify_google_search(query, num_results=num)
    if profiles:
        logger.debug(f"[CONTACTS] Found via Apify Google Search")
        return profiles

    # Strategy 2: Serper.dev fallback
    profiles = _serper_search(query, num=num)
    if profiles:
        logger.debug(f"[CONTACTS] Found via Serper fallback")
        return profiles

    # No results from any source
    return []


# ---------------------------------------------------------------------------
# AI-powered contact suggestion + profile lookup
# ---------------------------------------------------------------------------

CONTACT_SYSTEM_PROMPT = """You are a job search networking strategist. Given a job listing, suggest 3 types of people to connect with on LinkedIn.

For each, provide:
1. A specific job title to search for (e.g., "Engineering Manager", "Head of SRE")
2. Why connecting helps
3. A personalized connection message that mentions the specific role AND the company name

CONNECTION MESSAGE RULES (CRITICAL):
- Maximum 280 characters (LinkedIn limits connection notes to 300 chars, leave margin)
- Mention the specific role title and company name
- Do NOT use generic phrases like "I came across your profile" or "I would love to connect"
- Be specific about what you can offer or discuss
- Write as a human, not as an AI. Short, direct, conversational.

Return ONLY valid JSON:
{
    "contacts": [
        {
            "search_title": "<specific job title>",
            "role_type": "<hiring_manager|peer|recruiter>",
            "why": "<1 sentence>",
            "message": "<connection note, max 280 chars>"
        }
    ]
}

Provide exactly 3 contacts: hiring manager, peer, recruiter."""


def find_contacts(job: Job, ai_client: AIClient) -> list[dict]:
    """Find LinkedIn contacts with REAL profile URLs.

    Pipeline:
      1. AI suggests which roles to search for at the company
      2. Apify (or Serper fallback) finds actual LinkedIn profiles
      3. Returns contacts with real profile URLs and names

    Returns a list of contact dicts, each with:
      - name, role, role_type, why, message, profile_url, search_url, google_url
    """
    prompt = f"""Find the best LinkedIn contacts for this job application:

- Job Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description snippet: {job.description[:1500] if job.description else 'N/A'}

The candidate is applying for this role and wants to network at {job.company}."""

    # Step 1: AI suggests roles to search for
    search_roles = _get_search_roles(job, ai_client, prompt)

    # Step 2: Find REAL profiles via Apify / Serper
    contacts = []
    location = job.location.split(",")[0] if job.location else ""

    for role_info in search_roles:
        title = role_info["search_title"]

        profiles = _search_linkedin_profile(
            company=job.company,
            title=title,
            location=location,
            num=1,
        )

        if profiles:
            p = profiles[0]
            contacts.append({
                "name": p["name"],
                "role": p.get("title") or title,
                "role_type": role_info["role_type"],
                "why": role_info["why"],
                "message": role_info["message"],
                "profile_url": p["url"],
                "search_url": "",
                "google_url": "",
            })
            logger.info(f"[CONTACTS] Found {p['name']} ({title}) at {job.company}")
        else:
            # Fallback: provide Google search URL for manual lookup
            query = f'site:linkedin.com/in "{title}" "{job.company}"'
            if location:
                query += f" {location}"
            google_url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query})
            contacts.append({
                "name": "",
                "role": title,
                "role_type": role_info["role_type"],
                "why": role_info["why"],
                "message": role_info["message"],
                "profile_url": "",
                "search_url": "",
                "google_url": google_url,
            })

    found = sum(1 for c in contacts if c.get("profile_url"))
    logger.info(f"[CONTACTS] {job.company}: {found}/{len(contacts)} with real profile URLs")
    return contacts


def _get_search_roles(job: Job, ai_client: AIClient, prompt: str) -> list[dict]:
    """Use AI to suggest which roles to search for, with sensible defaults as fallback."""
    try:
        result_text = ai_client.complete(prompt=prompt, system=CONTACT_SYSTEM_PROMPT, temperature=0.4)
        data = extract_json(result_text)
        roles = []
        for c in data.get("contacts", []):
            message = c.get("message", "")
            # Truncate to LinkedIn's 300 char limit (with margin)
            if len(message) > 300:
                message = message[:297] + "..."
                logger.debug(f"[CONTACTS] Truncated connection message to 300 chars")
            roles.append({
                "search_title": c.get("search_title", ""),
                "role_type": c.get("role_type", "peer"),
                "why": c.get("why", ""),
                "message": message,
            })
        if roles:
            return roles
    except Exception as e:
        logger.warning(f"[CONTACTS] AI role suggestion failed for {job.company}: {e}")

    # Fallback defaults
    return [
        {
            "search_title": "Engineering Manager",
            "role_type": "hiring_manager",
            "why": "Likely the hiring manager",
            "message": f"Hi! I applied for the {job.title} role at {job.company}.",
        },
        {
            "search_title": "Technical Recruiter",
            "role_type": "recruiter",
            "why": "Can fast-track your application",
            "message": f"Hi! I'm interested in the {job.title} role at {job.company}.",
        },
        {
            "search_title": "Senior Engineer",
            "role_type": "peer",
            "why": "Potential peer on the team",
            "message": f"Hi! I applied for the {job.title} role at {job.company}.",
        },
    ]


def find_contacts_batch(jobs: list[Job], ai_client: AIClient) -> None:
    """Find LinkedIn contacts for a list of jobs. Mutates job objects in place.

    Budget with Apify Google Search actor:
      - 3 searches per job (1 per contact role)
      - For 25 jobs = 75 actor runs
      - Apify free tier: $5/month (~1,000 Google Search results)

    Falls back to Serper if Apify is not configured.
    """
    apify_available = bool(os.environ.get("APIFY_API_KEY"))
    serper_available = bool(os.environ.get("SERPER_API_KEY"))

    if not apify_available and not serper_available:
        logger.warning(
            "[CONTACTS] Neither APIFY_API_KEY nor SERPER_API_KEY set — "
            "contacts will only have Google search URLs"
        )

    for job in jobs:
        contacts = find_contacts(job, ai_client)
        job.linkedin_contacts = json.dumps(contacts)
