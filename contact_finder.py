"""LinkedIn contact finder — finds REAL profile URLs via Serper (Google Search API).

For each matched job, searches Google for actual LinkedIn profiles of
hiring managers, recruiters, and team members at the target company.
Returns real linkedin.com/in/ URLs with names and titles.

Uses Serper.dev (2,500 free queries/month). Set SERPER_API_KEY in .env.
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


def _serper_search(query: str, num: int = 3) -> List[Dict[str, str]]:
    """Search Google via Serper.dev API. Returns list of {name, url, title}."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("[CONTACTS] SERPER_API_KEY not set — can't find LinkedIn profiles")
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

            # Parse name and title from Google result title
            # Format: "FirstName LastName - Title at Company | LinkedIn"
            name = ""
            person_title = ""
            parts = re.split(r'\s*[-–—]\s*', title_text)
            if parts:
                name = parts[0].strip().replace(" | LinkedIn", "")
            if len(parts) >= 2:
                person_title = parts[1].strip().replace(" | LinkedIn", "").replace("LinkedIn", "").strip()

            profiles.append({
                "name": name,
                "url": link,
                "title": person_title,
            })

        return profiles

    except Exception as e:
        logger.warning(f"[CONTACTS] Serper search failed: {e}")
        return []


CONTACT_SYSTEM_PROMPT = """You are a job search networking strategist. Given a job listing, suggest 3 types of people to connect with on LinkedIn.

For each, provide:
1. A specific job title to search for (e.g., "Engineering Manager", "Head of SRE")
2. Why connecting helps
3. A personalized connection message (mention the specific role, no generic templates)

Return ONLY valid JSON:
{
    "contacts": [
        {
            "search_title": "<specific job title>",
            "role_type": "<hiring_manager|peer|recruiter>",
            "why": "<1 sentence>",
            "message": "<connection note>"
        }
    ]
}

Provide exactly 3 contacts: hiring manager, peer, recruiter."""


def find_contacts(job: Job, ai_client: AIClient) -> list[dict]:
    """Find LinkedIn contacts with REAL profile URLs via Serper.

    1. AI suggests which roles to search for
    2. Serper finds actual LinkedIn profiles for each role
    3. Returns contacts with real profile URLs and names
    """
    prompt = f"""Find the best LinkedIn contacts for this job application:

- Job Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description snippet: {job.description[:1500] if job.description else 'N/A'}

The candidate is applying for this role and wants to network at {job.company}."""

    # Step 1: AI suggests roles
    search_roles = []
    try:
        result_text = ai_client.complete(prompt=prompt, system=CONTACT_SYSTEM_PROMPT, temperature=0.4)
        data = extract_json(result_text)
        for c in data.get("contacts", []):
            search_roles.append({
                "search_title": c.get("search_title", ""),
                "role_type": c.get("role_type", "peer"),
                "why": c.get("why", ""),
                "message": c.get("message", ""),
            })
    except Exception as e:
        logger.warning(f"[CONTACTS] AI role suggestion failed for {job.company}: {e}")
        search_roles = [
            {"search_title": "Engineering Manager", "role_type": "hiring_manager",
             "why": "Likely the hiring manager",
             "message": f"Hi! I applied for the {job.title} role at {job.company}."},
            {"search_title": "Technical Recruiter", "role_type": "recruiter",
             "why": "Can fast-track your application",
             "message": f"Hi! I'm interested in the {job.title} role at {job.company}."},
            {"search_title": "Senior Engineer", "role_type": "peer",
             "why": "Potential peer on the team",
             "message": f"Hi! I applied for the {job.title} role at {job.company}."},
        ]

    # Step 2: Serper finds REAL profiles (1 API call per role = 3 calls per job)
    contacts = []
    location = job.location.split(",")[0] if job.location else ""

    for role_info in search_roles:
        title = role_info["search_title"]
        # Include location to find people in the right office
        query = f'site:linkedin.com/in "{title}" "{job.company}"'
        if location:
            query += f' {location}'

        profiles = _serper_search(query, num=1)

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
            # Fallback: provide Google search URL
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


def find_contacts_batch(jobs: list[Job], ai_client: AIClient) -> None:
    """Find LinkedIn contacts for a list of jobs. Mutates job objects in place.

    Budget: 3 Serper calls per job (1 per contact role).
    For 70 jobs = 210 calls (well within 2,500 free tier).
    """
    for job in jobs:
        contacts = find_contacts(job, ai_client)
        job.linkedin_contacts = json.dumps(contacts)
