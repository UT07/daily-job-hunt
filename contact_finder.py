"""LinkedIn contact finder for job applications.

For each matched job, generates LinkedIn search URLs to find relevant
people to connect with (hiring managers, recruiters, team leads).

NOTE: This does NOT scrape LinkedIn profiles (that violates ToS).
Instead, it constructs targeted search URLs that you can open in
your browser to find and connect with the right people.
"""

from __future__ import annotations
import json
import logging
import urllib.parse
from scrapers.base import Job
from ai_client import AIClient
from matcher import extract_json

logger = logging.getLogger(__name__)


CONTACT_SYSTEM_PROMPT = """You are a job search networking strategist. Given a job listing, identify the types of people the candidate should connect with on LinkedIn to improve their chances.

For each contact type, provide:
1. A descriptive role (e.g., "Engineering Manager", "Senior DevOps Engineer")
2. Why connecting with them helps
3. A suggested LinkedIn connection message (1-2 sentences, professional but warm)

Return ONLY valid JSON (no markdown, no code fences):
{
    "contacts": [
        {
            "role": "<role title to search for>",
            "why": "<1 sentence on why this connection helps>",
            "message": "<suggested LinkedIn connection note>"
        }
    ]
}

Provide exactly 3-4 contacts, prioritized by impact:
1. The likely hiring manager (most important)
2. A team member who'd be a peer
3. A recruiter at the company
4. (Optional) Someone in a senior/leadership role"""


def find_contacts(
    job: Job,
    ai_client: AIClient,
) -> list[dict]:
    """Generate LinkedIn search URLs and connection suggestions for a job.

    Returns list of contact dicts with role, why, message, and search_url.
    """
    prompt = f"""Find the best LinkedIn contacts for this job application:

- Job Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description snippet: {job.description[:1500]}

The candidate is applying for this role and wants to network with the right people at {job.company}."""

    contacts = []

    try:
        result_text = ai_client.complete(
            prompt=prompt,
            system=CONTACT_SYSTEM_PROMPT,
            temperature=0.4,
        )

        data = extract_json(result_text)

        for contact in data.get("contacts", []):
            role = contact.get("role", "")
            # Build LinkedIn search URL
            search_query = f"{role} {job.company}"
            search_url = (
                "https://www.linkedin.com/search/results/people/?"
                + urllib.parse.urlencode({
                    "keywords": search_query,
                    "origin": "GLOBAL_SEARCH_HEADER",
                })
            )

            contacts.append({
                "role": role,
                "why": contact.get("why", ""),
                "message": contact.get("message", ""),
                "search_url": search_url,
            })

        logger.info(f"[CONTACTS] {job.company}: found {len(contacts)} contact suggestions")

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[CONTACTS] Error finding contacts for {job.company}: {e}")
        # Fallback: generate basic search URLs without AI
        contacts = _fallback_contacts(job)

    return contacts


def _fallback_contacts(job: Job) -> list[dict]:
    """Generate basic LinkedIn search URLs without AI."""
    roles = [
        ("Engineering Manager", "Likely the hiring manager"),
        ("Technical Recruiter", "Can fast-track your application"),
        ("Senior Engineer", "Potential peer on the team"),
    ]

    contacts = []
    for role, why in roles:
        search_query = f"{role} {job.company}"
        search_url = (
            "https://www.linkedin.com/search/results/people/?"
            + urllib.parse.urlencode({
                "keywords": search_query,
                "origin": "GLOBAL_SEARCH_HEADER",
            })
        )
        contacts.append({
            "role": role,
            "why": why,
            "message": f"Hi! I came across the {job.title} role at {job.company} and would love to connect.",
            "search_url": search_url,
        })

    return contacts


def find_contacts_batch(
    jobs: list[Job],
    ai_client: AIClient,
) -> None:
    """Find LinkedIn contacts for a list of jobs. Mutates job objects in place."""
    for job in jobs:
        contacts = find_contacts(job, ai_client)
        job.linkedin_contacts = json.dumps(contacts)
