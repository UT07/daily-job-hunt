"""LinkedIn job scraper — multi-strategy approach.

LinkedIn is the most heavily protected job board. This scraper uses
three strategies in order of reliability:

1. SerpAPI Google Jobs (indexes LinkedIn listings) — most reliable
2. LinkedIn's public job search page via Playwright stealth
3. LinkedIn RSS feeds (limited but never blocked)

Direct LinkedIn scraping is a cat-and-mouse game. Expect occasional
failures. The SerpAPI fallback ensures you always get LinkedIn jobs.
"""

from __future__ import annotations
import html
import logging
import re
import urllib.parse
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)

# Max jobs to fetch full descriptions for per query (speed + anti-detection)
_MAX_DESCRIPTION_FETCHES = 8  # LinkedIn auth-walls after ~5-10 page loads

# Selectors for the job description container on LinkedIn job detail pages.
# LinkedIn A/B tests layouts frequently — try these in order.
_DESCRIPTION_SELECTORS = [
    ".description__text",
    ".show-more-less-html__markup",
    "[class*='description__text']",
    ".jobs-description__content",
    ".jobs-box__html-content",
    "[class*='jobs-description']",
    "article",  # fallback — the whole article
]


def _strip_html(raw: str) -> str:
    """Remove HTML tags, decode entities, and normalise whitespace."""
    # Remove script/style blocks
    raw = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities
    raw = html.unescape(raw)
    # Collapse whitespace
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


class LinkedInScraper(BaseScraper):
    """Scrapes LinkedIn jobs via Playwright stealth browser.

    WARNING: LinkedIn has the most aggressive anti-scraping. This scraper:
    - Does NOT require login (uses public job search)
    - Uses stealth browser with anti-detection
    - Rate-limits heavily (5-10s between pages)
    - May still get blocked occasionally

    For reliable LinkedIn coverage, also enable the SerpAPI scraper which
    indexes LinkedIn postings via Google Jobs.
    """

    name = "linkedin"

    def __init__(self, max_pages: int = 2, geo_id: str = "104738515"):
        self.max_pages = max_pages
        # 104738515 = Ireland, 101165590 = UK, 103644278 = US
        self.geo_id = geo_id

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        return run_async(self._search_async(query, location, days_back))

    async def _search_async(self, query: str, location: str, days_back: int) -> List[Job]:
        jobs = []

        # LinkedIn time filter: r86400 = 24h, r604800 = week, r2592000 = month
        if days_back <= 1:
            time_filter = "r86400"
        elif days_back <= 7:
            time_filter = "r604800"
        else:
            time_filter = "r2592000"

        # Determine geoId
        geo_id = self.geo_id
        loc_lower = location.lower()
        if "dublin" in loc_lower:
            geo_id = "104738515"  # Ireland
        elif any(w in loc_lower for w in ["uk", "london", "england"]):
            geo_id = "101165590"
        elif any(w in loc_lower for w in ["remote", "worldwide", "global"]):
            geo_id = ""  # No geo filter for remote

        # Experience level: 1=Internship, 2=Entry, 3=Associate, 4=Mid-Senior
        experience_levels = "1%2C2%2C3"  # Entry + Associate + Mid-Senior

        async with stealth_browser() as browser:
            page = await browser.new_page()

            for page_num in range(self.max_pages):
                start = page_num * 25
                params = {
                    "keywords": query,
                    "f_TPR": time_filter,
                    "f_E": experience_levels,
                    "sortBy": "DD",  # Date descending
                    "start": start,
                    "position": 1,
                    "pageNum": page_num,
                }
                if geo_id:
                    params["geoId"] = geo_id
                if location and "remote" in loc_lower:
                    params["f_WT"] = "2"  # Remote filter

                url = f"https://www.linkedin.com/jobs/search/?{urllib.parse.urlencode(params)}"
                logger.info(f"[LinkedIn] Scraping: {query} (page {page_num + 1})")

                success = await browser.safe_goto(page, url, wait_until="domcontentloaded", timeout=15000)
                if not success:
                    logger.warning(f"[LinkedIn] Blocked or failed on page {page_num + 1}")
                    break

                await browser.human_delay(3000, 6000)
                await browser.scroll_page(page, scrolls=4)
                await browser.human_delay(2000, 4000)

                # Parse job cards
                try:
                    cards = await page.query_selector_all('.base-card, .job-search-card, [class*="jobs-search__results-list"] li')

                    if not cards:
                        cards = await page.query_selector_all('.base-search-card, [data-entity-urn]')

                    if not cards:
                        # Check if we hit the auth wall
                        content = await page.content()
                        if "authwall" in content.lower() or "sign in" in content.lower():
                            logger.warning("[LinkedIn] Hit auth wall — LinkedIn is blocking this session")
                            break
                        logger.info(f"[LinkedIn] No cards found on page {page_num + 1}")
                        break

                    for card in cards:
                        try:
                            job = await self._parse_card(card)
                            if job:
                                jobs.append(job)
                        except Exception:
                            continue

                except Exception as e:
                    logger.error(f"[LinkedIn] Error parsing page {page_num + 1}: {e}")
                    break

                # Extra delay for LinkedIn — they're watching
                await browser.human_delay(5000, 10000)

            # Fetch descriptions for the top N jobs (still inside browser context).
            # Navigate to each job URL directly — more reliable than the side panel.
            # Stop immediately if LinkedIn shows an auth wall.
            if jobs:
                to_fetch = jobs[:_MAX_DESCRIPTION_FETCHES]
                logger.info(f"[LinkedIn] Fetching descriptions for {len(to_fetch)} jobs...")
                desc_page = await browser.new_page()
                fetched = 0
                for i, job in enumerate(to_fetch):
                    if not job.apply_url:
                        continue
                    try:
                        desc = await self._fetch_description(browser, desc_page, job.apply_url)
                        if desc == "__AUTH_WALL__":
                            logger.warning(f"[LinkedIn] Auth wall hit — stopping description fetch ({fetched} fetched)")
                            break
                        if desc:
                            job.description = desc
                            fetched += 1
                    except Exception as e:
                        logger.warning(f"[LinkedIn] Description fetch failed for job {i + 1}: {e}")
                    # Generous delay — LinkedIn watches inter-request timing
                    await browser.human_delay(4000, 10000)
                try:
                    await desc_page.close()
                except Exception:
                    pass

        logger.info(f"[LinkedIn] Found {len(jobs)} jobs for '{query}'")
        return self.deduplicate(jobs)

    async def _parse_card(self, card) -> Job | None:
        """Parse a LinkedIn job card from public search."""
        # Title
        title_el = await card.query_selector('.base-search-card__title, [class*="job-search-card__title"], h3')
        title = (await title_el.inner_text()).strip() if title_el else None
        if not title:
            return None

        # Link
        link_el = await card.query_selector('a.base-card__full-link, a[class*="base-card"], a[href*="/jobs/view/"]')
        href = await link_el.get_attribute("href") if link_el else ""
        if href:
            # Clean tracking params
            href = href.split("?")[0] if "?" in href else href

        # Company
        company_el = await card.query_selector('[class*="base-search-card__subtitle"], h4, [class*="company"]')
        company = (await company_el.inner_text()).strip() if company_el else "Unknown"

        # Location
        loc_el = await card.query_selector('[class*="job-search-card__location"], [class*="base-search-card__metadata"]')
        location = (await loc_el.inner_text()).strip() if loc_el else ""

        # Date
        date_el = await card.query_selector('time, [class*="listed-time"], [datetime]')
        posted = ""
        if date_el:
            posted = await date_el.get_attribute("datetime") or (await date_el.inner_text()).strip()

        is_remote = any(w in (title + location).lower() for w in ["remote", "work from home"])

        return Job(
            title=title,
            company=company,
            location=location,
            description="",  # Full description fetched separately by _fetch_description
            apply_url=href,
            source="linkedin",
            posted_date=posted,
            remote=is_remote,
        )

    async def _fetch_description(self, browser, page, url: str) -> str:
        """Navigate to a LinkedIn job detail page and extract the description text.

        LinkedIn renders the description inside a collapsible section.  We try
        multiple CSS selectors in order of specificity and fall back gracefully.

        Returns the stripped plain-text description (up to 4000 chars), or ""
        if nothing could be extracted.
        """
        success = await browser.safe_goto(page, url, wait_until="domcontentloaded", timeout=20000)
        if not success:
            return ""

        # Brief wait for dynamic content to settle
        await browser.human_delay(2000, 4000)

        # Check for auth wall — if we're being blocked, signal caller to stop
        try:
            content = await page.content()
            if "authwall" in content.lower() or "sign-in" in content.lower():
                return "__AUTH_WALL__"
        except Exception:
            pass

        # Try to click "Show more" / "See more" to expand the full description
        for expand_selector in [
            "button.show-more-less-html__button",
            "button[aria-label*='more']",
            "[class*='show-more-less'] button",
            "button.jobs-description__footer-button",
        ]:
            try:
                btn = await page.query_selector(expand_selector)
                if btn:
                    await btn.click()
                    await browser.human_delay(800, 1500)
                    break
            except Exception:
                pass

        # Extract description text using selector fallbacks
        for selector in _DESCRIPTION_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    raw_html = await el.inner_html()
                    text = _strip_html(raw_html)
                    if len(text) > 100:  # sanity-check: ignore trivially short matches
                        return text[:4000]
            except Exception:
                continue

        # Last-resort: pull the whole page body and hope for the best
        try:
            body_el = await page.query_selector("body")
            if body_el:
                raw_html = await body_el.inner_html()
                text = _strip_html(raw_html)
                if len(text) > 200:
                    return text[:4000]
        except Exception:
            pass

        return ""
