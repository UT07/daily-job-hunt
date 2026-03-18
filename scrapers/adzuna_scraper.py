"""Adzuna API scraper - excellent for Ireland/UK job market."""

from __future__ import annotations
import logging
import requests
import time
import threading
from datetime import datetime, timedelta
from typing import List
from .base import BaseScraper, Job

logger = logging.getLogger(__name__)


class AdzunaScraper(BaseScraper):
    """Scrapes job listings via Adzuna API.

    Strong coverage for Ireland, UK, and EU markets.
    Free tier: 250 requests/month.
    Sign up: https://developer.adzuna.com/
    """

    name = "adzuna"

    # Class-level rate limiter — Adzuna free tier is very strict
    _rate_lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(self, app_id: str, app_key: str, delay: float = 2.0):
        self.app_id = app_id
        self.app_key = app_key
        self.delay = delay
        self.base_url = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    def _rate_wait(self):
        """Ensure minimum 2s between Adzuna requests across all threads."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - AdzunaScraper._last_request_time
            if elapsed < 2.0:
                time.sleep(2.0 - elapsed)
            AdzunaScraper._last_request_time = time.time()

    def search(self, query: str, location: str, days_back: int = 1, **kwargs) -> List[Job]:
        self._rate_wait()
        jobs = []

        # Determine country code from location
        country = "ie"  # default Ireland
        loc_lower = location.lower()
        if any(w in loc_lower for w in ["uk", "united kingdom", "london", "england"]):
            country = "gb"
        elif any(w in loc_lower for w in ["us", "united states", "new york", "san francisco"]):
            country = "us"
        elif any(w in loc_lower for w in ["germany", "berlin", "munich"]):
            country = "de"
        elif any(w in loc_lower for w in ["netherlands", "amsterdam"]):
            country = "nl"

        # For remote searches, search Ireland + global
        is_remote_search = "remote" in loc_lower

        url = self.base_url.format(country=country, page=1)
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query,
            "results_per_page": 20,
            "max_days_old": days_back,
            "sort_by": "date",
        }

        # Add location filter (not for remote searches)
        if not is_remote_search and location:
            clean_loc = location.replace(", Ireland", "").replace(", UK", "").strip()
            if clean_loc.lower() not in ["ireland", "remote"]:
                params["where"] = clean_loc

        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 404:
                # Don't spam logs — just silently skip
                return []
            if resp.status_code == 429:
                logger.warning(f"[Adzuna] Rate limited for '{query}' — backing off 5s")
                time.sleep(5)
                return []
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("results", []):
                loc_display = item.get("location", {}).get("display_name", "")
                title = item.get("title", "").replace("<strong>", "").replace("</strong>", "")
                desc = item.get("description", "").replace("<strong>", "").replace("</strong>", "")

                is_remote = any(w in title.lower() + desc.lower() for w in ["remote", "work from home", "wfh"])

                salary = ""
                sal_min = item.get("salary_min")
                sal_max = item.get("salary_max")
                if sal_min and sal_max:
                    currency = "€" if country == "ie" else "£" if country == "gb" else "$"
                    salary = f"{currency}{sal_min:,.0f} - {currency}{sal_max:,.0f}"
                elif sal_min:
                    currency = "€" if country == "ie" else "£" if country == "gb" else "$"
                    salary = f"{currency}{sal_min:,.0f}+"

                jobs.append(Job(
                    title=title,
                    company=item.get("company", {}).get("display_name", ""),
                    location=loc_display,
                    description=desc,
                    apply_url=item.get("redirect_url", ""),
                    source="adzuna",
                    posted_date=item.get("created", ""),
                    salary=salary,
                    job_type=item.get("contract_type", ""),
                    remote=is_remote or is_remote_search,
                ))

            if jobs:
                logger.info(f"[Adzuna] '{query}' in '{location}' -> {len(jobs)} jobs")

        except requests.RequestException as e:
            logger.error(f"[Adzuna] Error searching '{query}' in '{location}': {e}")
        except (KeyError, IndexError) as e:
            logger.error(f"[Adzuna] Parse error for '{query}': {e}")

        return self.deduplicate(jobs)
