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


CONTACT_SYSTEM_PROMPT = """You are a job search networking strategist. Given a job listing, identify SPECIFIC people the candidate should connect with on LinkedIn.

Your goal is to find the most likely REAL people at the company — not generic role titles. Use your knowledge of the company's org structure, public team pages, engineering blog authors, and conference speakers to suggest actual names when possible.

For each contact, provide:
1. A specific name if you can reasonably guess one (e.g., "John Smith" or "VP of Engineering" if name unknown)
2. Their exact title at the company
3. Why connecting with them helps
4. A personalized LinkedIn connection message (reference something specific — a blog post, talk, or the team's work)
5. A Google search query to find their LinkedIn profile (e.g., "site:linkedin.com/in John Smith CompanyName")

Return ONLY valid JSON (no markdown, no code fences):
{
    "contacts": [
        {
            "name": "<full name if known, or 'Unknown' if guessing>",
            "title": "<their specific job title at the company>",
            "role_type": "<hiring_manager|peer|recruiter|leader>",
            "why": "<1 sentence on why this connection helps>",
            "message": "<personalized LinkedIn connection note — reference something specific>",
            "google_search": "<site:linkedin.com/in query to find this person>"
        }
    ]
}

Provide exactly 3-4 contacts, prioritized by impact:
1. The likely hiring manager (most important — try to name them)
2. A team member who'd be a peer (someone in a similar role at the company)
3. A recruiter at the company (try to find the actual talent acquisition person)
4. (Optional) A senior/leadership figure whose work you can reference

IMPORTANT: Be specific. "Engineering Manager at Mars Capital" is better than just "Engineering Manager". If you know the company well enough to guess a name, include it — the candidate will verify before reaching out."""


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
            name = contact.get("name", "")
            title = contact.get("title", "") or contact.get("role", "")
            role_type = contact.get("role_type", "peer")
            google_query = contact.get("google_search", "")

            # Build targeted LinkedIn search URL with company filter
            if name and name != "Unknown":
                search_keywords = f"{name} {job.company}"
            else:
                search_keywords = f"{title} {job.company}"

            linkedin_search_url = (
                "https://www.linkedin.com/search/results/people/?"
                + urllib.parse.urlencode({
                    "keywords": search_keywords,
                    "origin": "GLOBAL_SEARCH_HEADER",
                })
            )

            # Google search fallback for finding specific profiles
            if not google_query:
                if name and name != "Unknown":
                    google_query = f"site:linkedin.com/in {name} {job.company}"
                else:
                    google_query = f"site:linkedin.com/in {title} {job.company}"

            google_search_url = (
                "https://www.google.com/search?"
                + urllib.parse.urlencode({"q": google_query})
            )

            contacts.append({
                "name": name if name != "Unknown" else "",
                "role": title,
                "role_type": role_type,
                "why": contact.get("why", ""),
                "message": contact.get("message", ""),
                "search_url": linkedin_search_url,
                "google_url": google_search_url,
            })

        logger.info(f"[CONTACTS] {job.company}: found {len(contacts)} contact suggestions")

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[CONTACTS] Error finding contacts for {job.company}: {e}")
        # Fallback: generate basic search URLs without AI
        contacts = _fallback_contacts(job)

    return contacts


def _fallback_contacts(job: Job) -> list[dict]:
    """Generate targeted LinkedIn search URLs without AI."""
    roles = [
        ("Engineering Manager", "hiring_manager", "Likely the hiring manager"),
        ("Technical Recruiter", "recruiter", "Can fast-track your application"),
        ("Senior Engineer", "peer", "Potential peer on the team"),
    ]

    contacts = []
    for role, role_type, why in roles:
        search_query = f"{role} {job.company}"
        search_url = (
            "https://www.linkedin.com/search/results/people/?"
            + urllib.parse.urlencode({
                "keywords": search_query,
                "origin": "GLOBAL_SEARCH_HEADER",
            })
        )
        google_url = (
            "https://www.google.com/search?"
            + urllib.parse.urlencode({"q": f"site:linkedin.com/in {role} {job.company}"})
        )
        contacts.append({
            "name": "",
            "role": role,
            "role_type": role_type,
            "why": why,
            "message": f"Hi! I came across the {job.title} role at {job.company} and would love to connect.",
            "search_url": search_url,
            "google_url": google_url,
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
