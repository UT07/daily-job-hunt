"""LinkedIn contact finder — finds REAL profile URLs via Google search.

For each matched job, searches Google for actual LinkedIn profiles of
hiring managers, recruiters, and team members at the target company.
Returns real linkedin.com/in/ URLs, not generic search pages.
"""

from __future__ import annotations
import json
import logging
import re
import time
import urllib.parse
from typing import List, Dict
from scrapers.base import Job
from ai_client import AIClient
from matcher import extract_json

logger = logging.getLogger(__name__)


def _search_linkedin_profiles(role: str, company: str, max_results: int = 1) -> List[Dict[str, str]]:
    """Search LinkedIn People Search for profiles matching role + company.

    Uses Playwright stealth browser (same as LinkedIn job scraper).
    Returns list of {"name": "John Smith", "url": "https://linkedin.com/in/...", "title": "..."}
    """
    profiles = []

    try:
        from scrapers.browser import get_browser_context

        search_query = f"{role} {company}"
        search_url = (
            "https://www.linkedin.com/search/results/people/?"
            + urllib.parse.urlencode({
                "keywords": search_query,
                "origin": "GLOBAL_SEARCH_HEADER",
            })
        )

        with get_browser_context(headless=True) as (browser, context):
            page = context.new_page()
            try:
                page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(3)  # Wait for results to render

                # Extract profile cards from LinkedIn search results
                # Each result has: name, headline, profile URL
                result_cards = page.query_selector_all('div.entity-result__item, li.reusable-search__result-container')

                if not result_cards:
                    # Try broader selectors
                    result_cards = page.query_selector_all('a[href*="/in/"]')

                for card in result_cards[:max_results]:
                    try:
                        # Try to get structured data
                        name_el = card.query_selector('span[aria-hidden="true"], .entity-result__title-text')
                        name = name_el.inner_text().strip().split('\n')[0] if name_el else ""

                        link_el = card.query_selector('a[href*="/in/"]') if card.tag_name != 'a' else card
                        href = link_el.get_attribute('href') if link_el else ""

                        headline_el = card.query_selector('.entity-result__primary-subtitle, .entity-result__summary')
                        title = headline_el.inner_text().strip().split('\n')[0] if headline_el else role

                        if href and '/in/' in href:
                            # Clean up URL
                            profile_url = href.split('?')[0]
                            if not profile_url.startswith('http'):
                                profile_url = 'https://www.linkedin.com' + profile_url

                            # Extract name from URL if not found in DOM
                            if not name:
                                slug = profile_url.split('/in/')[-1].rstrip('/')
                                slug = re.sub(r'-[a-f0-9]{5,}$', '', slug)
                                name = slug.replace('-', ' ').title()

                            profiles.append({
                                "name": name,
                                "url": profile_url,
                                "title": title,
                            })
                    except Exception:
                        continue

                page.close()

            except Exception as e:
                logger.debug(f"[CONTACTS] LinkedIn search page failed: {e}")
                page.close()

        time.sleep(2)  # Rate limit between searches

    except ImportError:
        logger.warning("[CONTACTS] Playwright not available — can't search LinkedIn profiles")
    except Exception as e:
        logger.warning(f"[CONTACTS] LinkedIn profile search failed for '{role}' at '{company}': {e}")

    return profiles


CONTACT_SYSTEM_PROMPT = """You are a job search networking strategist. Given a job listing, identify the types of people the candidate should connect with on LinkedIn.

For each contact type, provide:
1. A specific job title to search for (e.g., "Engineering Manager", "Head of SRE", "Technical Recruiter")
2. Why connecting with them helps
3. A personalized LinkedIn connection message (1-2 sentences, mention the specific role)

Return ONLY valid JSON (no markdown, no code fences):
{
    "contacts": [
        {
            "search_title": "<specific job title to search for on LinkedIn>",
            "role_type": "<hiring_manager|peer|recruiter|leader>",
            "why": "<1 sentence on why this connection helps>",
            "message": "<suggested LinkedIn connection note — mention the specific role>"
        }
    ]
}

Provide exactly 3 contacts, prioritized by impact:
1. The likely hiring manager (most important)
2. A team member who'd be a peer
3. A recruiter at the company"""


def find_contacts(
    job: Job,
    ai_client: AIClient,
) -> list[dict]:
    """Find LinkedIn contacts with REAL profile URLs via Google search.

    1. AI suggests which roles to search for
    2. Google search finds actual LinkedIn profiles for each role
    3. Returns contacts with real profile URLs
    """
    prompt = f"""Find the best LinkedIn contacts for this job application:

- Job Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description snippet: {job.description[:1500]}

The candidate is applying for this role and wants to network with the right people at {job.company}."""

    contacts = []

    # Step 1: AI suggests which roles to search for
    search_roles = []
    try:
        result_text = ai_client.complete(
            prompt=prompt,
            system=CONTACT_SYSTEM_PROMPT,
            temperature=0.4,
        )
        data = extract_json(result_text)

        for contact in data.get("contacts", []):
            search_roles.append({
                "search_title": contact.get("search_title", contact.get("role", "")),
                "role_type": contact.get("role_type", "peer"),
                "why": contact.get("why", ""),
                "message": contact.get("message", ""),
            })
    except Exception as e:
        logger.warning(f"[CONTACTS] AI role suggestion failed for {job.company}: {e}")
        # Fallback roles
        search_roles = [
            {"search_title": "Engineering Manager", "role_type": "hiring_manager",
             "why": "Likely the hiring manager", "message": f"Hi! I applied for the {job.title} role at {job.company}."},
            {"search_title": "Technical Recruiter", "role_type": "recruiter",
             "why": "Can fast-track your application", "message": f"Hi! I'm interested in the {job.title} role at {job.company}."},
            {"search_title": "Senior Engineer", "role_type": "peer",
             "why": "Potential peer on the team", "message": f"Hi! I applied for the {job.title} role at {job.company}."},
        ]

    # Step 2: Google search for REAL LinkedIn profiles for each role
    for role_info in search_roles:
        search_title = role_info["search_title"]
        profiles = _google_search_linkedin_profiles(search_title, job.company, max_results=1)

        if profiles:
            p = profiles[0]
            contacts.append({
                "name": p["name"],
                "role": p.get("title") or search_title,
                "role_type": role_info["role_type"],
                "why": role_info["why"],
                "message": role_info["message"],
                "profile_url": p["url"],  # REAL LinkedIn profile URL
                "search_url": "",  # Not needed — we have the real URL
                "google_url": "",
            })
        else:
            # Fallback: provide the Google search URL so user can find manually
            google_query = f'site:linkedin.com/in "{search_title}" "{job.company}"'
            google_url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": google_query})
            contacts.append({
                "name": "",
                "role": search_title,
                "role_type": role_info["role_type"],
                "why": role_info["why"],
                "message": role_info["message"],
                "profile_url": "",
                "search_url": "",
                "google_url": google_url,
            })

    logger.info(f"[CONTACTS] {job.company}: {len(contacts)} contacts "
                f"({sum(1 for c in contacts if c.get('profile_url'))} with real profile URLs)")
    return contacts


def find_contacts_batch(
    jobs: list[Job],
    ai_client: AIClient,
) -> None:
    """Find LinkedIn contacts for a list of jobs. Mutates job objects in place."""
    for job in jobs:
        contacts = find_contacts(job, ai_client)
        job.linkedin_contacts = json.dumps(contacts)
