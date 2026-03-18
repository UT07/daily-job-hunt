"""Indeed scraper using Playwright stealth browser.

Indeed uses Cloudflare and aggressive bot detection.
This scraper uses a real browser with stealth patches to bypass protections.
"""

from __future__ import annotations
import logging
import re
import urllib.parse
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)


class IndeedScraper(BaseScraper):
    """Scrapes Indeed job listings via Playwright.

    Uses stealth browser to handle Cloudflare protection.
    Rate-limited to avoid detection.
    """

    name = "indeed"

    def __init__(self, country: str = "ie", max_pages: int = 2):
        self.country = country  # ie, co.uk, com, etc.
        self.max_pages = max_pages
        self.base_urls = {
            "ie": "https://ie.indeed.com",
            "co.uk": "https://www.indeed.co.uk",
            "com": "https://www.indeed.com",
        }

    def search(self, query: str, location: str, days_back: int = 1, **kwargs) -> List[Job]:
        return run_async(self._search_async(query, location, days_back))

    async def _search_async(self, query: str, location: str, days_back: int) -> List[Job]:
        jobs = []
        base = self.base_urls.get(self.country, self.base_urls["ie"])

        # Map days_back to Indeed's fromage parameter
        fromage = min(days_back, 3)  # 1, 3, 7, 14 are valid

        async with stealth_browser() as browser:
            page = await browser.new_page()

            for page_num in range(self.max_pages):
                start = page_num * 10
                params = urllib.parse.urlencode({
                    "q": query,
                    "l": location.replace(", Ireland", "").replace(", Remote", ""),
                    "fromage": fromage,
                    "sort": "date",
                    "start": start,
                })
                url = f"{base}/jobs?{params}"

                logger.info(f"[Indeed] Scraping: {query} in {location} (page {page_num + 1})")

                success = await browser.safe_goto(page, url)
                if not success:
                    logger.warning(f"[Indeed] Failed to load page {page_num + 1}")
                    break

                await browser.human_delay(2000, 4000)
                await browser.scroll_page(page, scrolls=2)

                # Extract job cards
                try:
                    cards = await page.query_selector_all('[class*="job_seen_beacon"], [class*="resultContent"], .tapItem')
                    if not cards:
                        # Try alternative selectors (Indeed changes these often)
                        cards = await page.query_selector_all('[data-jk]')

                    if not cards:
                        logger.info(f"[Indeed] No job cards found on page {page_num + 1}")
                        break

                    for card in cards:
                        try:
                            job = await self._parse_card(card, page, base)
                            if job:
                                jobs.append(job)
                        except Exception as e:
                            continue

                except Exception as e:
                    logger.error(f"[Indeed] Error parsing page {page_num + 1}: {e}")
                    break

                await browser.human_delay(3000, 7000)

        logger.info(f"[Indeed] Found {len(jobs)} jobs for '{query}'")
        return self.deduplicate(jobs)

    async def _parse_card(self, card, page, base_url: str) -> Job | None:
        """Parse a single Indeed job card element."""
        # Title
        title_el = await card.query_selector('h2 a, h2 span, [class*="jobTitle"] a, [class*="jobTitle"] span')
        title = (await title_el.inner_text()).strip() if title_el else None
        if not title:
            return None

        # Company
        company_el = await card.query_selector('[data-testid="company-name"], [class*="companyName"], .company')
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        # Location
        loc_el = await card.query_selector('[data-testid="text-location"], [class*="companyLocation"], .location')
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        # Apply link
        link_el = await card.query_selector('h2 a, a[data-jk], a[id^="job_"]')
        href = await link_el.get_attribute("href") if link_el else ""
        if href and not href.startswith("http"):
            href = base_url + href
        # Extract job key for direct link
        jk = await card.get_attribute("data-jk") or ""
        if jk:
            apply_url = f"{base_url}/viewjob?jk={jk}"
        elif href:
            apply_url = href
        else:
            apply_url = ""

        # Salary (if shown)
        sal_el = await card.query_selector('[class*="salary"], [class*="estimated-salary"], .salaryText')
        salary = (await sal_el.inner_text()).strip() if sal_el else ""

        # Snippet/description
        snippet_el = await card.query_selector('[class*="snippet"], .job-snippet, [class*="underShelfFooter"]')
        snippet = (await snippet_el.inner_text()).strip() if snippet_el else ""

        # Date
        date_el = await card.query_selector('[class*="date"], .date, [class*="myJobsState"]')
        posted = (await date_el.inner_text()).strip() if date_el else ""

        is_remote = any(w in (title + location + snippet).lower() for w in ["remote", "work from home", "wfh", "hybrid"])

        return Job(
            title=title,
            company=company,
            location=location,
            description=snippet,
            apply_url=apply_url,
            source="indeed",
            posted_date=posted,
            salary=salary,
            remote=is_remote,
        )
