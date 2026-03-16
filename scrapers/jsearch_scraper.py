"""JSearch scraper via RapidAPI - good free tier (200 req/mo)."""

from __future__ import annotations
import requests
import time
import threading
from datetime import datetime, timedelta
from typing import List
from .base import BaseScraper, Job


class JSearchScraper(BaseScraper):
    """Scrapes job listings via JSearch API on RapidAPI.

    Aggregates from LinkedIn, Indeed, Glassdoor, ZipRecruiter, and more.
    Free tier: 200 requests/month. Paid: $30/mo for 10,000 requests.
    Sign up: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
    """

    name = "jsearch"

    # Class-level rate limiter — max 5 requests per second across all threads
    _rate_lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(self, api_key: str, delay: float = 2.0):
        self.api_key = api_key
        self.delay = delay
        self.base_url = "https://jsearch.p.rapidapi.com/search"

    def _rate_wait(self):
        """Ensure minimum 1.5s between requests across all threads."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - JSearchScraper._last_request_time
            if elapsed < 1.5:
                time.sleep(1.5 - elapsed)
            JSearchScraper._last_request_time = time.time()

    def search(self, query: str, location: str, days_back: int = 1, **kwargs) -> List[Job]:
        self._rate_wait()
        jobs = []
        # Build date filter
        date_posted = "today" if days_back <= 1 else "3days" if days_back <= 3 else "week"

        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }

        params = {
            "query": f"{query} in {location}",
            "page": "1",
            "num_pages": "1",
            "date_posted": date_posted,
            "remote_jobs_only": "false",
        }

        # If searching for remote jobs specifically
        if "remote" in location.lower():
            params["remote_jobs_only"] = "true"
            params["query"] = query

        try:
            resp = requests.get(self.base_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("data", []):
                is_remote = item.get("job_is_remote", False)
                location_str = ""
                city = item.get("job_city", "")
                country = item.get("job_country", "")
                state = item.get("job_state", "")
                parts = [p for p in [city, state, country] if p]
                location_str = ", ".join(parts) if parts else ("Remote" if is_remote else "Unknown")

                # Salary info
                salary = ""
                sal_min = item.get("job_min_salary")
                sal_max = item.get("job_max_salary")
                sal_currency = item.get("job_salary_currency", "")
                sal_period = item.get("job_salary_period", "")
                if sal_min and sal_max:
                    salary = f"{sal_currency}{sal_min:,.0f} - {sal_currency}{sal_max:,.0f} {sal_period}"
                elif sal_min:
                    salary = f"{sal_currency}{sal_min:,.0f}+ {sal_period}"

                # Experience level
                exp = item.get("job_required_experience", {})
                exp_level = ""
                if exp:
                    exp_level = exp.get("experience_level", "")
                    no_exp = exp.get("no_experience_required", False)
                    if no_exp:
                        exp_level = "entry_level"

                jobs.append(Job(
                    title=item.get("job_title", ""),
                    company=item.get("employer_name", ""),
                    location=location_str,
                    description=item.get("job_description", ""),
                    apply_url=item.get("job_apply_link", ""),
                    source="jsearch",
                    posted_date=item.get("job_posted_at_datetime_utc", ""),
                    salary=salary,
                    job_type=item.get("job_employment_type", ""),
                    experience_level=exp_level,
                    remote=is_remote,
                ))

            time.sleep(self.delay)
        except requests.RequestException as e:
            print(f"[JSearch] Error searching '{query}' in '{location}': {e}")
        except (KeyError, IndexError) as e:
            print(f"[JSearch] Parse error for '{query}': {e}")

        return self.deduplicate(jobs)
