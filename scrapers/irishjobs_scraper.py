"""IrishJobs.ie scraper using Playwright stealth browser.

IrishJobs is one of Ireland's largest job boards.
Moderate Cloudflare protection.
"""

from __future__ import annotations
import urllib.parse
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async


class IrishJobsScraper(BaseScraper):
    """Scrapes IrishJobs.ie via Playwright."""

    name = "irishjobs"
    BASE_URL = "https://www.irishjobs.ie"

    def __init__(self, max_pages: int = 2):
        self.max_pages = max_pages

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        return run_async(self._search_async(query, location, days_back))

    async def _search_async(self, query: str, location: str, days_back: int) -> List[Job]:
        jobs = []

        async with stealth_browser() as browser:
            page = await browser.new_page()

            for page_num in range(1, self.max_pages + 1):
                # IrishJobs URL structure
                params = {
                    "Keywords": query,
                    "autosuggestEndpoint": "/autosuggest",
                    "page": page_num,
                }
                if location and location.lower() not in ["ireland", "remote"]:
                    params["Location"] = location.replace(", Ireland", "")

                # Date filter: 1 = last 24h, 3 = last 3 days, 7 = last week
                if days_back <= 1:
                    params["postedWithin"] = "1"
                elif days_back <= 3:
                    params["postedWithin"] = "3"
                else:
                    params["postedWithin"] = "7"

                url = f"{self.BASE_URL}/Jobs?{urllib.parse.urlencode(params)}"
                print(f"  [IrishJobs] Scraping: {query} (page {page_num})")

                success = await browser.safe_goto(page, url)
                if not success:
                    print(f"  [IrishJobs] Failed to load page {page_num}")
                    break

                await browser.human_delay(2000, 4000)
                await browser.scroll_page(page, scrolls=2)

                # Parse job listings
                try:
                    cards = await page.query_selector_all('.job-card, [class*="SearchResult"], .lister__item, article[class*="job"]')

                    if not cards:
                        # Try broader selectors
                        cards = await page.query_selector_all('.job, [data-job-id], .search-result')

                    if not cards:
                        print(f"  [IrishJobs] No cards found on page {page_num}")
                        break

                    for card in cards:
                        try:
                            job = await self._parse_card(card)
                            if job:
                                jobs.append(job)
                        except Exception:
                            continue

                except Exception as e:
                    print(f"  [IrishJobs] Error parsing page {page_num}: {e}")
                    break

                await browser.human_delay(3000, 6000)

        print(f"  [IrishJobs] Found {len(jobs)} jobs for '{query}'")
        return self.deduplicate(jobs)

    async def _parse_card(self, card) -> Job | None:
        """Parse a single IrishJobs job card."""
        # Title + link
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

        # Company
        company_el = await card.query_selector('[class*="company"], [class*="Company"], .job-company')
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        # Location
        loc_el = await card.query_selector('[class*="location"], [class*="Location"], .job-location')
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        # Salary
        sal_el = await card.query_selector('[class*="salary"], [class*="Salary"]')
        salary = (await sal_el.inner_text()).strip() if sal_el else ""

        # Snippet
        snippet_el = await card.query_selector('[class*="description"], [class*="snippet"], .job-description')
        snippet = (await snippet_el.inner_text()).strip()[:500] if snippet_el else ""

        # Date
        date_el = await card.query_selector('[class*="date"], [class*="Date"], time')
        posted = (await date_el.inner_text()).strip() if date_el else ""

        is_remote = any(w in (title + location + snippet).lower() for w in ["remote", "work from home", "wfh"])

        return Job(
            title=title,
            company=company,
            location=location,
            description=snippet,
            apply_url=href,
            source="irishjobs",
            posted_date=posted,
            salary=salary,
            remote=is_remote,
        )
