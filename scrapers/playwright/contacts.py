"""Contacts finder using Scrapling StealthyFetcher.

Searches Google for LinkedIn profiles of hiring managers, recruiters,
and team leads at companies with matched jobs. Replaces Apify Google Search.
"""
import json
import logging
import os
import re
from urllib.parse import quote_plus

from scrapers.playwright.base import BaseScraper, human_delay

logger = logging.getLogger(__name__)

SEARCH_ROLES = [
    "Engineering Manager",
    "Technical Recruiter",
    "Senior Software Engineer",
]


class Scraper(BaseScraper):
    SOURCE = "contacts"
    MAX_JOBS = 50  # max jobs to find contacts for
    MIN_DELAY = 5.0
    MAX_DELAY = 10.0
    USE_PROXY = True

    def scrape(self, queries: list[str]) -> list[dict]:
        """Find contacts for matched jobs. Queries param is ignored — reads from jobs table."""
        from scrapling import StealthyFetcher

        user_id = os.environ.get("USER_ID", "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39")

        # Get matched jobs that need contacts
        result = self.db.table("jobs").select("job_id, title, company") \
            .eq("user_id", user_id) \
            .not_.is_("resume_s3_url", "null") \
            .is_("linkedin_contacts", "null") \
            .limit(self.MAX_JOBS) \
            .execute()

        jobs_needing_contacts = result.data or []
        if not jobs_needing_contacts:
            logger.info("[contacts] No jobs need contacts")
            return []

        logger.info(f"[contacts] Finding contacts for {len(jobs_needing_contacts)} jobs")

        fetcher = StealthyFetcher()

        for job in jobs_needing_contacts:
            contacts = []
            company = job["company"]

            for role in SEARCH_ROLES:
                if self._circuit_break():
                    break

                query = f'site:linkedin.com/in "{company}" "{role}"'
                url = f"https://www.google.com/search?q={quote_plus(query)}&num=3"

                try:
                    proxy_cfg = {"server": self.proxy_url} if self.proxy_url else None
                    page = fetcher.fetch(url, headless=True, proxy=proxy_cfg, timeout=30)
                    profiles = self._extract_google_results(page, role)
                    contacts.extend(profiles)
                    self.consecutive_failures = 0
                except Exception as e:
                    logger.warning(f"[contacts] Google search failed for {company} {role}: {e}")
                    self.consecutive_failures += 1

                human_delay(self.MIN_DELAY, self.MAX_DELAY)

            if contacts:
                self._save_contacts(job["job_id"], contacts)
                self.jobs_found += 1

        return []  # Contacts are saved directly, not via _save_job

    def _extract_google_results(self, page, role: str) -> list[dict]:
        """Extract LinkedIn profile info from Google search results."""
        contacts = []

        # Try to find search result links
        try:
            links = page.find_all("a[href*='linkedin.com/in/']")
            for link in links[:3]:
                href = link.get("href", "")
                # Extract clean LinkedIn URL
                li_match = re.search(r'(https?://\w+\.linkedin\.com/in/[^&?"\s]+)', href)
                if not li_match:
                    continue

                profile_url = li_match.group(1)
                # Get the text content for name
                text = link.get_text(strip=True)
                name = text.split(" - ")[0].strip() if " - " in text else text[:50]

                contacts.append({
                    "name": name,
                    "role": role,
                    "role_type": "hiring_manager" if "Manager" in role else "recruiter" if "Recruiter" in role else "team_member",
                    "why": f"{role} at the company — likely involved in hiring",
                    "message": f"Hi {name.split()[0] if name else 'there'}, I noticed a role at your company that aligns with my background. Would love to connect and learn more about the team.",
                    "profile_url": profile_url,
                    "search_url": f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(name)}",
                    "google_url": "",
                })
        except Exception as e:
            logger.warning(f"[contacts] Parse failed: {e}")

        return contacts

    def _save_contacts(self, job_id: str, contacts: list[dict]):
        """Save contacts to the jobs table."""
        try:
            contacts_json = json.dumps(contacts)
            self.db.table("jobs").update({"linkedin_contacts": contacts_json}).eq("job_id", job_id).execute()
            logger.info(f"[contacts] Saved {len(contacts)} contacts for job {job_id}")
        except Exception as e:
            logger.error(f"[contacts] Save failed for {job_id}: {e}")
