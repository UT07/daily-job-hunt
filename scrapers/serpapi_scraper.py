"""Google Jobs scraper via SerpAPI - the most reliable aggregator."""

from __future__ import annotations
import logging
import requests
import time
from typing import List
from .base import BaseScraper, Job

logger = logging.getLogger(__name__)


class SerpAPIScraper(BaseScraper):
    """Scrapes Google Jobs results via SerpAPI.

    Google Jobs aggregates from LinkedIn, Indeed, Glassdoor, company career
    pages, and dozens of other boards — making this the single best source.

    Pricing: ~$50/mo for 5000 searches. Free tier: 100 searches.
    Sign up: https://serpapi.com/
    """

    name = "serpapi"

    def __init__(self, api_key: str, delay: float = 2.0):
        self.api_key = api_key
        self.delay = delay
        self.base_url = "https://serpapi.com/search"

    def search(self, query: str, location: str, days_back: int = 1, **kwargs) -> List[Job]:
        jobs = []
        # date_posted parameter: "today" = last 24h, "3days", "week", "month"
        date_filter = "today" if days_back <= 1 else "3days" if days_back <= 3 else "week"

        params = {
            "engine": "google_jobs",
            "q": query,
            "location": location,
            "chips": f"date_posted:{date_filter}",
            "api_key": self.api_key,
            "num": 20,
        }

        try:
            resp = requests.get(self.base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("jobs_results", []):
                description = item.get("description", "")
                highlights = item.get("job_highlights", [])
                for hl in highlights:
                    for line in hl.get("items", []):
                        description += f"\n- {line}"

                # Determine if remote
                location_raw = item.get("location", "")
                is_remote = any(w in location_raw.lower() for w in ["remote", "anywhere", "work from home"])

                # Extract apply link
                apply_options = item.get("apply_options", [])
                apply_url = apply_options[0]["link"] if apply_options else ""

                # Extract salary if available
                salary = ""
                detected = item.get("detected_extensions", {})
                if detected.get("salary"):
                    salary = detected["salary"]

                jobs.append(Job(
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=location_raw,
                    description=description,
                    apply_url=apply_url,
                    source="serpapi",
                    posted_date=item.get("detected_extensions", {}).get("posted_at", ""),
                    salary=salary,
                    job_type=detected.get("schedule_type", ""),
                    remote=is_remote,
                ))

            time.sleep(self.delay)
        except requests.RequestException as e:
            logger.error(f"[SerpAPI] Error searching '{query}' in '{location}': {e}")
        except (KeyError, IndexError) as e:
            logger.error(f"[SerpAPI] Parse error for '{query}': {e}")

        return self.deduplicate(jobs)
