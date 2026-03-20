"""IrishJobs.ie scraper.

NOTE: IrishJobs.ie is behind Akamai Bot Manager which blocks both
headless browsers and plain HTTP requests with JS challenges.
This scraper attempts requests-based parsing first (fast, works if
Akamai is relaxed) and falls back to Playwright if needed.

For reliable Irish job coverage, use Adzuna API (strong IE coverage)
or SerpAPI (indexes IrishJobs via Google Jobs).
"""

from __future__ import annotations
import logging
import re
import requests as http_requests
import urllib.parse
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)


class IrishJobsScraper(BaseScraper):
    """Scrapes IrishJobs.ie — requests-first with Playwright fallback.

    IrishJobs uses aggressive Akamai bot protection. This scraper will
    often return 0 results. Enable Adzuna/SerpAPI for reliable Irish coverage.
    """

    name = "irishjobs"
    BASE_URL = "https://www.irishjobs.ie"
    # IrishJobs also has an API-like endpoint for their React frontend
    API_URL = "https://www.irishjobs.ie/api/search"

    def __init__(self, max_pages: int = 2):
        self.max_pages = max_pages

    _site_reachable: bool | None = None  # Class-level: skip all queries if site is down

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        # Fast bail: if site already known to be unreachable, skip immediately
        if IrishJobsScraper._site_reachable is False:
            return []

        # Try requests-based approach first (fast, no browser)
        try:
            jobs = self._search_requests(query, location, days_back)
            if jobs:
                IrishJobsScraper._site_reachable = True
                return self.deduplicate(jobs)
        except requests.exceptions.Timeout:
            logger.warning(f"[IrishJobs] Timeout — marking site unreachable for this run")
            IrishJobsScraper._site_reachable = False
            return []
        except Exception as e:
            logger.warning(f"[IrishJobs] Requests approach failed: {e}")

        # Fallback to Playwright (only if requests didn't timeout)
        try:
            jobs = run_async(self._search_browser(query, location, days_back))
            if jobs:
                IrishJobsScraper._site_reachable = True
            return self.deduplicate(jobs)
        except Exception as e:
            logger.error(f"[IrishJobs] Browser approach also failed: {e}")
            IrishJobsScraper._site_reachable = False
            return []

    def _search_requests(self, query: str, location: str, days_back: int) -> List[Job]:
        """Try fetching jobs via direct HTTP (works when Akamai is relaxed)."""
        jobs = []

        params = {
            "Keywords": query,
            "page": 1,
        }
        if location and location.lower() not in ["ireland", "remote"]:
            params["Location"] = location.replace(", Ireland", "")
        if days_back <= 1:
            params["postedWithin"] = "1"
        elif days_back <= 3:
            params["postedWithin"] = "3"
        else:
            params["postedWithin"] = "7"

        resp = http_requests.get(
            f"{self.BASE_URL}/Jobs",
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html",
            },
            timeout=30,
        )

        # Check if we got the Akamai challenge page instead of real content
        if "bm-verify" in resp.text or "akamai" in resp.text.lower():
            logger.warning("[IrishJobs] Blocked by Akamai bot protection")
            return []

        # Parse job listings from HTML
        # Look for job card patterns in the response
        job_links = re.findall(
            r'href="(/Jobs/[^"]+)"[^>]*>([^<]+)</a>',
            resp.text,
        )

        for href, title in job_links:
            title = title.strip()
            if len(title) < 5:
                continue
            full_url = self.BASE_URL + href
            jobs.append(Job(
                title=title,
                company="",  # Hard to extract without JS rendering
                location=location or "Ireland",
                description="",
                apply_url=full_url,
                source="irishjobs",
            ))

        if jobs:
            logger.info(f"[IrishJobs] Found {len(jobs)} jobs for '{query}' (requests)")
        return jobs

    async def _search_browser(self, query: str, location: str, days_back: int) -> List[Job]:
        """Fallback: Playwright stealth with longer timeouts for Akamai."""
        jobs = []

        async with stealth_browser() as browser:
            page = await browser.new_page()

            params = {
                "Keywords": query,
                "page": 1,
            }
            if location and location.lower() not in ["ireland", "remote"]:
                params["Location"] = location.replace(", Ireland", "")
            if days_back <= 1:
                params["postedWithin"] = "1"
            elif days_back <= 3:
                params["postedWithin"] = "3"
            else:
                params["postedWithin"] = "7"

            url = f"{self.BASE_URL}/Jobs?{urllib.parse.urlencode(params)}"
            logger.info(f"[IrishJobs] Scraping: {query} (browser)")

            success = await browser.safe_goto(page, url, timeout=45000)
            if not success:
                logger.warning("[IrishJobs] Blocked by Akamai — try enabling Adzuna/SerpAPI for Irish coverage")
                return jobs

            await browser.human_delay(2000, 4000)
            await browser.scroll_page(page, scrolls=2)

            cards = await page.query_selector_all('.job-card, [class*="SearchResult"], .lister__item, article[class*="job"], .job, [data-job-id]')

            for card in cards:
                try:
                    job = await self._parse_card(card)
                    if job:
                        jobs.append(job)
                except Exception:
                    continue

        logger.info(f"[IrishJobs] Found {len(jobs)} jobs for '{query}'")
        return jobs

    async def _parse_card(self, card) -> Job | None:
        """Parse a single IrishJobs job card."""
        title_el = await card.query_selector('a[class*="title"], h2 a, .job-title a, a[class*="JobTitle"]')
        if not title_el:
            title_el = await card.query_selector("a")
        if not title_el:
            return None

        title = (await title_el.inner_text()).strip()
        href = await title_el.get_attribute("href") or ""
        if href and not href.startswith("http"):
            href = self.BASE_URL + href

        if not title or len(title) < 3:
            return None

        company_el = await card.query_selector('[class*="company"], [class*="Company"], .job-company')
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        loc_el = await card.query_selector('[class*="location"], [class*="Location"], .job-location')
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        sal_el = await card.query_selector('[class*="salary"], [class*="Salary"]')
        salary = (await sal_el.inner_text()).strip() if sal_el else ""

        snippet_el = await card.query_selector('[class*="description"], [class*="snippet"], .job-description')
        snippet = (await snippet_el.inner_text()).strip()[:500] if snippet_el else ""

        is_remote = any(w in (title + location + snippet).lower() for w in ["remote", "work from home", "wfh"])

        return Job(
            title=title,
            company=company,
            location=location,
            description=snippet,
            apply_url=href,
            source="irishjobs",
            salary=salary,
            remote=is_remote,
        )
