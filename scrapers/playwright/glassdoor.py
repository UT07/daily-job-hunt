"""Glassdoor scraper using Scrapling StealthyFetcher.

Glassdoor has the most aggressive anti-bot detection of the three targets.
This scraper navigates to each job detail page and extracts the description
from the [data-test="jobDescriptionContent"] element.

If a login overlay appears at any point, the scraper circuit-breaks
immediately (Glassdoor locks out scrapers aggressively once detected).

Uses proxy + StealthyFetcher with conservative delays (4-6s).
"""

import logging
import urllib.parse

from scrapers.playwright.base import BaseScraper, human_delay

logger = logging.getLogger(__name__)


class GlassdoorScraper(BaseScraper):
    """Scrapes Glassdoor job listings via Scrapling StealthyFetcher.

    Strategy:
    1. Load search results page.
    2. Extract job card links.
    3. Navigate to each detail page.
    4. Extract full JD from [data-test="jobDescriptionContent"].
    5. If login overlay detected, stop immediately (circuit break).

    Volume cap: 30 jobs. Delays: 4-6 seconds.
    """

    SOURCE = "glassdoor"
    MAX_JOBS = 30
    MIN_DELAY = 4.0
    MAX_DELAY = 6.0
    MAX_CONSECUTIVE_FAILURES = 3
    USE_PROXY = True

    BASE_URL = "https://www.glassdoor.com"

    def __init__(self):
        super().__init__()
        self.max_pages = 2  # 30 results/page * 2 = ~60 candidates, capped at 30

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------
    def _build_search_url(self, query: str, location: str, page: int = 1) -> str:
        """Build Glassdoor job search URL.

        Glassdoor uses a path-based format:
        /Job/{location}-{query}-jobs-SRCH_KO0,{len}.htm?fromAge=3
        """
        # Clean query for URL path
        q_slug = query.strip().replace(" ", "-").lower()
        loc_slug = location.strip().replace(" ", "-").lower()

        params = {
            "fromAge": 3,  # last 3 days
            "sortBy": "date_desc",
        }
        if page > 1:
            params["p"] = page

        path = f"/Job/{loc_slug}-{q_slug}-jobs-SRCH_KO0,{len(query)}.htm"
        return f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}"

    # ------------------------------------------------------------------
    # Login overlay detection
    # ------------------------------------------------------------------
    def _is_login_blocked(self, page) -> bool:
        """Detect if Glassdoor is showing a login overlay/modal.

        When detected, we must stop scraping entirely -- Glassdoor
        escalates blocking after login walls appear.
        """
        if not page:
            return False

        page_text = page.text or ""

        # Check for login modal indicators
        login_indicators = [
            'id="LoginModal"',
            'data-test="login-modal"',
            'class="loginModal"',
            "Sign in to view",
            "Create a free account",
            "Sign Up to Continue",
        ]

        for indicator in login_indicators:
            if indicator in page_text:
                return True

        # Check for login modal via CSS selectors
        login_modal = page.css_first("#LoginModal") or page.css_first("[data-test='login-modal']") or page.css_first("#HardsellOverlay")
        if login_modal:
            return True

        return False

    # ------------------------------------------------------------------
    # Job card extraction from search results
    # ------------------------------------------------------------------
    def _extract_job_links(self, page) -> list[dict]:
        """Extract job card metadata and detail page URLs from search results."""
        job_links = []

        # Glassdoor job cards
        cards = (
            page.css("li.react-job-listing")
            or page.css("[data-test='jobListing']")
            or page.css("li[data-id]")
            or page.css("ul.jobs-list > li")
        )

        if not cards:
            logger.warning("[glassdoor] No job cards found on search results page")
            return job_links

        for card in cards:
            try:
                # Title and link
                title_el = (
                    card.css_first("a[data-test='job-title']")
                    or card.css_first("a.jobTitle")
                    or card.css_first("a[class*='JobCard_jobTitle']")
                )
                title = ""
                detail_url = ""
                if title_el:
                    title = title_el.text.strip()
                    href = title_el.attrib.get("href", "")
                    if href:
                        detail_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

                # Company
                company_el = (
                    card.css_first("[data-test='emp-name']")
                    or card.css_first("span.EmployerProfile_compactEmployerName__LE242")
                    or card.css_first("div.employer-name")
                )
                company = company_el.text.strip() if company_el else ""

                # Location
                loc_el = (
                    card.css_first("[data-test='emp-location']")
                    or card.css_first("span[class*='location']")
                    or card.css_first("div.location")
                )
                location = loc_el.text.strip() if loc_el else ""

                # Salary (may not be present)
                salary_el = (
                    card.css_first("[data-test='detailSalary']")
                    or card.css_first("span[class*='salary']")
                )
                salary = salary_el.text.strip() if salary_el else None

                if title and detail_url:
                    job_links.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "detail_url": detail_url,
                        "salary": salary,
                    })
            except Exception as e:
                logger.debug(f"[glassdoor] Failed to parse job card: {e}")
                continue

        logger.info(f"[glassdoor] Found {len(job_links)} job links on search page")
        return job_links

    # ------------------------------------------------------------------
    # Detail page extraction
    # ------------------------------------------------------------------
    def _extract_detail(self, fetcher, job_meta: dict) -> dict | None:
        """Fetch a job detail page and extract the full description.

        Returns a complete job dict or None if extraction fails.
        """
        url = job_meta["detail_url"]

        try:
            # timeout=30 to avoid Scrapling issue #100 Turnstile hang
            page = fetcher.fetch(
                url,
                headless=True,
                proxy={"server": self.proxy_url} if self.proxy_url else None,
                timeout=30,
            )

            if not page or not page.status == 200:
                logger.warning(f"[glassdoor] Bad response for detail page: {url}")
                return None

            # Check for login wall -- circuit break if found
            if self._is_login_blocked(page):
                logger.warning("[glassdoor] Login overlay detected -- circuit breaking")
                # Set consecutive_failures high enough to trigger circuit break
                self.consecutive_failures = self.MAX_CONSECUTIVE_FAILURES
                return None

            # Primary selector: [data-test="jobDescriptionContent"]
            desc_el = (
                page.css_first('[data-test="jobDescriptionContent"]')
                or page.css_first("div.jobDescriptionContent")
                or page.css_first("div#JobDescriptionContainer")
                or page.css_first("section.job-description")
            )

            if not desc_el:
                logger.debug(f"[glassdoor] No description element found at {url}")
                return None

            description = desc_el.text.strip()

            if not description or len(description) < 50:
                logger.debug(f"[glassdoor] Description too short at {url}")
                return None

            # Extract job type from detail page
            job_type = None
            type_el = (
                page.css_first("[data-test='empType']")
                or page.css_first("span[class*='jobType']")
            )
            if type_el:
                job_type = type_el.text.strip()

            return {
                "title": job_meta["title"],
                "company": job_meta["company"],
                "location": job_meta["location"],
                "description": description,
                "apply_url": url,
                "salary": job_meta.get("salary"),
                "job_type": job_type,
                "description_quality": "full",
            }

        except Exception as e:
            logger.error(f"[glassdoor] Error fetching detail page {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Main scrape method
    # ------------------------------------------------------------------
    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape Glassdoor for the given queries.

        For each query:
        1. Fetch search results page(s).
        2. Extract job card links.
        3. Visit each detail page for full JD.
        4. Circuit break on login overlay.
        """
        from scrapling import StealthyFetcher

        location = __import__("os").environ.get("SCRAPE_LOCATION", "Ireland")
        all_jobs = []

        fetcher = StealthyFetcher()

        for query in queries:
            if len(all_jobs) >= self.MAX_JOBS or self._circuit_break():
                break

            for page_num in range(1, self.max_pages + 1):
                if len(all_jobs) >= self.MAX_JOBS or self._circuit_break():
                    break

                url = self._build_search_url(query, location, page_num)
                logger.info(f"[glassdoor] Fetching search page {page_num} for '{query}': {url}")

                try:
                    # timeout=30 for Turnstile
                    page = fetcher.fetch(
                        url,
                        headless=True,
                        proxy={"server": self.proxy_url} if self.proxy_url else None,
                        timeout=30,
                    )

                    if not page or not page.status == 200:
                        status = page.status if page else "no response"
                        logger.warning(f"[glassdoor] Bad search response: {status}")
                        self.consecutive_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    # Check for login wall on search page
                    if self._is_login_blocked(page):
                        logger.warning("[glassdoor] Login overlay on search page -- stopping")
                        self.consecutive_failures = self.MAX_CONSECUTIVE_FAILURES
                        return all_jobs

                    # Extract job links from search results
                    job_links = self._extract_job_links(page)

                    if not job_links:
                        self.consecutive_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    self.consecutive_failures = 0

                    # Visit each detail page
                    for job_meta in job_links:
                        if len(all_jobs) >= self.MAX_JOBS or self._circuit_break():
                            break

                        human_delay(self.MIN_DELAY, self.MAX_DELAY)

                        job = self._extract_detail(fetcher, job_meta)
                        if job:
                            all_jobs.append(job)
                            self.consecutive_failures = 0
                            logger.info(f"[glassdoor] Got: {job['title']} at {job['company']}")
                        else:
                            self.consecutive_failures += 1

                except Exception as e:
                    logger.error(f"[glassdoor] Error on search page {page_num}: {e}")
                    self.consecutive_failures += 1

                human_delay(self.MIN_DELAY, self.MAX_DELAY)

        logger.info(f"[glassdoor] Total jobs scraped: {len(all_jobs)}")
        return all_jobs[:self.MAX_JOBS]
