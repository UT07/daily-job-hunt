"""Glassdoor scraper using Playwright stealth browser.

Glassdoor uses aggressive bot detection (Cloudflare + Akamai).
This scraper uses Playwright stealth to scrape their public job search.

URL pattern: https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}&locT=C&locId={locId}
"""

from __future__ import annotations
import logging
import re
import urllib.parse
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)

# Glassdoor location IDs for key markets
_LOC_IDS = {
    "dublin": ("C2382571", "Dublin, Ireland"),
    "ireland": ("N120", "Ireland"),
    "london": ("C2671300", "London, UK"),
    "india": ("N115", "India"),
    "bangalore": ("C2940381", "Bangalore, India"),
    "remote": ("", "Remote"),
    "us": ("N1", "United States"),
    "new york": ("C1132348", "New York, NY"),
    "san francisco": ("C1147401", "San Francisco, CA"),
}


def _resolve_location(location: str) -> tuple[str, str]:
    """Map a location string to Glassdoor (locId, locType).

    Returns (locId, locType) where locType is C=city, N=nation, S=state.
    """
    loc_lower = location.lower().replace(", ireland", "").replace(", india", "").strip()
    for key, (loc_id, _) in _LOC_IDS.items():
        if key in loc_lower:
            loc_type = "C" if loc_id.startswith("C") else "N" if loc_id.startswith("N") else ""
            return loc_id.lstrip("CNS"), loc_type
    # Default: search without location filter
    return "", ""


class GlassdoorScraper(BaseScraper):
    """Scrapes Glassdoor job listings via Playwright.

    Uses stealth browser to handle Cloudflare/Akamai protection.
    Glassdoor's public search pages are accessible without login.
    """

    name = "glassdoor"

    def __init__(self, max_pages: int = 1):
        self.max_pages = max_pages

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        return run_async(self._search_async(query, location, days_back))

    async def _search_async(self, query: str, location: str, days_back: int) -> List[Job]:
        jobs = []
        loc_id, loc_type = _resolve_location(location)

        # Build Glassdoor search URL
        params = {"sc.keyword": query}
        if loc_id:
            params["locId"] = loc_id
            params["locT"] = loc_type
        # Date filter: fromAge=1 (24h), 3, 7, 14, 30
        if days_back <= 1:
            params["fromAge"] = "1"
        elif days_back <= 3:
            params["fromAge"] = "3"
        elif days_back <= 7:
            params["fromAge"] = "7"
        else:
            params["fromAge"] = "14"

        async with stealth_browser() as browser:
            page = await browser.new_page()

            for page_num in range(self.max_pages):
                if page_num > 0:
                    params["p"] = str(page_num + 1)

                url = f"https://www.glassdoor.com/Job/jobs.htm?{urllib.parse.urlencode(params)}"
                logger.info(f"[Glassdoor] Scraping: {query} in {location} (page {page_num + 1})")

                success = await browser.safe_goto(page, url, timeout=30000)
                if not success:
                    logger.warning(f"[Glassdoor] Failed to load page {page_num + 1}")
                    break

                await browser.human_delay(2000, 4000)
                await browser.scroll_page(page, scrolls=2)

                try:
                    # Glassdoor job cards — try multiple selector patterns
                    cards = await page.query_selector_all(
                        '[data-test="jobListing"], '
                        '.JobsList_jobListItem__wjTHv, '
                        'li[data-jobid], '
                        '[class*="JobCard"], '
                        '.react-job-listing'
                    )

                    if not cards:
                        # Fallback: any list item with a job link
                        cards = await page.query_selector_all('li a[href*="/job-listing/"]')
                        if cards:
                            # Wrap link elements — parse differently
                            for card in cards:
                                job = await self._parse_link(card)
                                if job:
                                    jobs.append(job)
                            break

                    if not cards:
                        logger.info(f"[Glassdoor] No job cards found on page {page_num + 1}")
                        break

                    for card in cards:
                        try:
                            job = await self._parse_card(card)
                            if job:
                                jobs.append(job)
                        except Exception:
                            continue

                except Exception as e:
                    logger.error(f"[Glassdoor] Error parsing page {page_num + 1}: {e}")
                    break

                await browser.human_delay(3000, 6000)

        logger.info(f"[Glassdoor] Found {len(jobs)} jobs for '{query}'")
        return self.deduplicate(jobs)

    async def _parse_card(self, card) -> Job | None:
        """Parse a Glassdoor job card element."""
        # Title — multiple selector patterns
        title_el = await card.query_selector(
            '[data-test="job-title"], '
            'a[class*="JobCard_jobTitle"], '
            'a[class*="jobTitle"], '
            '.job-title, '
            'a[href*="/job-listing/"]'
        )
        title = (await title_el.inner_text()).strip() if title_el else None
        if not title or len(title) < 3:
            return None

        # Apply URL
        href = ""
        if title_el:
            href = await title_el.get_attribute("href") or ""
        if href and not href.startswith("http"):
            href = "https://www.glassdoor.com" + href

        # Company
        company_el = await card.query_selector(
            '[data-test="emp-name"], '
            '[class*="EmployerProfile"], '
            '[class*="employer-name"], '
            '.job-search-company-name'
        )
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        # Location
        loc_el = await card.query_selector(
            '[data-test="emp-location"], '
            '[class*="location"], '
            '.job-search-location'
        )
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        # Salary
        sal_el = await card.query_selector(
            '[data-test="detailSalary"], '
            '[class*="salary"], '
            '[class*="SalaryEstimate"]'
        )
        salary = (await sal_el.inner_text()).strip() if sal_el else ""

        is_remote = "remote" in (title + location).lower()

        return Job(
            title=title,
            company=company,
            location=location,
            description="",
            apply_url=href,
            source="glassdoor",
            salary=salary,
            remote=is_remote,
        )

    async def _parse_link(self, link_el) -> Job | None:
        """Parse a bare job link (fallback when card selectors don't match)."""
        title = (await link_el.inner_text()).strip()
        if not title or len(title) < 5:
            return None

        href = await link_el.get_attribute("href") or ""
        if href and not href.startswith("http"):
            href = "https://www.glassdoor.com" + href

        return Job(
            title=title,
            company="",
            location="",
            description="",
            apply_url=href,
            source="glassdoor",
        )
