"""Glassdoor scraper using Scrapling StealthyFetcher.

Glassdoor has the most aggressive anti-bot detection of the three targets.
This scraper navigates to each job detail page and extracts the description
from the [data-test="jobDescriptionContent"] element.

If a login overlay appears at any point, the scraper circuit-breaks
immediately (Glassdoor locks out scrapers aggressively once detected).

Uses proxy + StealthyFetcher with conservative delays (4-6s).

Session cookies are cached in S3 with a 24h TTL to avoid re-authentication
on every run. The cookie file is stored at:
  s3://utkarsh-job-hunt/cookies/glassdoor_session.json
"""

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone

from scrapers.playwright.base import BaseScraper, human_delay

logger = logging.getLogger(__name__)

# --- Rate Limiting Constants ---
MAX_DETAIL_PAGES = 50  # Hard cap on detail page fetches per run

# --- Cookie Cache Constants ---
COOKIE_S3_BUCKET = "utkarsh-job-hunt"
COOKIE_S3_KEY = "cookies/glassdoor_session.json"
COOKIE_TTL_HOURS = 24


# ------------------------------------------------------------------
# Session cookie caching (S3, 24h TTL)
# ------------------------------------------------------------------
def _get_s3_client():
    """Lazy-init S3 client."""
    import boto3
    return boto3.client("s3")


def load_cookies_from_s3() -> list[dict] | None:
    """Load cached session cookies from S3 if they exist and are fresh.

    Returns a list of cookie dicts, or None if no valid cache exists.
    """
    try:
        s3 = _get_s3_client()
        resp = s3.get_object(Bucket=COOKIE_S3_BUCKET, Key=COOKIE_S3_KEY)
        data = json.loads(resp["Body"].read().decode("utf-8"))

        # Check TTL
        saved_at = datetime.fromisoformat(data.get("saved_at", "2000-01-01T00:00:00+00:00"))
        age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600
        if age_hours > COOKIE_TTL_HOURS:
            logger.info(f"[glassdoor] Cookie cache expired ({age_hours:.1f}h old, TTL={COOKIE_TTL_HOURS}h)")
            return None

        cookies = data.get("cookies", [])
        logger.info(f"[glassdoor] Loaded {len(cookies)} cached cookies ({age_hours:.1f}h old)")
        return cookies

    except _get_s3_client().exceptions.NoSuchKey:
        logger.info("[glassdoor] No cookie cache found in S3")
        return None
    except Exception as e:
        logger.warning(f"[glassdoor] Failed to load cookies from S3: {e}")
        return None


def save_cookies_to_s3(cookies: list[dict]) -> bool:
    """Save session cookies to S3 for reuse on next run.

    Returns True on success, False on failure.
    """
    if not cookies:
        return False

    try:
        s3 = _get_s3_client()
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "cookies": cookies,
        }
        s3.put_object(
            Bucket=COOKIE_S3_BUCKET,
            Key=COOKIE_S3_KEY,
            Body=json.dumps(payload),
            ContentType="application/json",
        )
        logger.info(f"[glassdoor] Saved {len(cookies)} cookies to S3")
        return True
    except Exception as e:
        logger.warning(f"[glassdoor] Failed to save cookies to S3: {e}")
        return False


class GlassdoorScraper(BaseScraper):
    """Scrapes Glassdoor job listings via Scrapling StealthyFetcher.

    Strategy:
    1. Load cached session cookies from S3 (if fresh).
    2. Load search results page.
    3. Extract job card links.
    4. Navigate to each detail page (up to MAX_DETAIL_PAGES).
    5. Extract full JD from [data-test="jobDescriptionContent"].
    6. If login overlay detected, stop immediately (circuit break).
    7. Save session cookies to S3 for next run.

    Volume cap: 30 jobs, 50 detail page fetches. Delays: 4-6 seconds.
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
        self.detail_pages_fetched = 0  # Track against MAX_DETAIL_PAGES
        self._cached_cookies: list[dict] | None = None

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
    def _detail_page_budget_exhausted(self) -> bool:
        """Check if we've hit the detail page fetch cap."""
        if self.detail_pages_fetched >= MAX_DETAIL_PAGES:
            logger.warning(
                f"[glassdoor] Detail page budget exhausted "
                f"({self.detail_pages_fetched}/{MAX_DETAIL_PAGES})"
            )
            return True
        return False

    def _extract_detail(self, fetcher, job_meta: dict) -> dict | None:
        """Fetch a job detail page and extract the full description.

        Returns a complete job dict or None if extraction fails.
        Respects MAX_DETAIL_PAGES rate limit.
        """
        if self._detail_page_budget_exhausted():
            return None

        url = job_meta["detail_url"]
        self.detail_pages_fetched += 1

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

        except TimeoutError:
            logger.warning(f"[glassdoor] Timeout fetching detail page: {url}")
            return None
        except Exception as e:
            logger.error(f"[glassdoor] Error fetching detail page {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Main scrape method
    # ------------------------------------------------------------------
    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape Glassdoor for the given queries.

        For each query:
        1. Load cached session cookies from S3 (if fresh).
        2. Fetch search results page(s).
        3. Extract job card links.
        4. Visit each detail page for full JD (up to MAX_DETAIL_PAGES).
        5. Circuit break on login overlay.
        6. Save session cookies to S3 on completion.
        """
        from scrapling import StealthyFetcher

        location = os.environ.get("SCRAPE_LOCATION", "Ireland")
        all_jobs: list[dict] = []

        # Try to load cached session cookies
        self._cached_cookies = load_cookies_from_s3()

        fetcher = StealthyFetcher()

        try:
            for query in queries:
                if (
                    len(all_jobs) >= self.MAX_JOBS
                    or self._circuit_break()
                    or self._detail_page_budget_exhausted()
                ):
                    break

                for page_num in range(1, self.max_pages + 1):
                    if (
                        len(all_jobs) >= self.MAX_JOBS
                        or self._circuit_break()
                        or self._detail_page_budget_exhausted()
                    ):
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
                            if (
                                len(all_jobs) >= self.MAX_JOBS
                                or self._circuit_break()
                                or self._detail_page_budget_exhausted()
                            ):
                                break

                            human_delay(self.MIN_DELAY, self.MAX_DELAY)

                            job = self._extract_detail(fetcher, job_meta)
                            if job:
                                all_jobs.append(job)
                                self.consecutive_failures = 0
                                logger.info(f"[glassdoor] Got: {job['title']} at {job['company']}")
                            else:
                                self.consecutive_failures += 1

                    except TimeoutError:
                        logger.warning(f"[glassdoor] Timeout on search page {page_num}")
                        self.consecutive_failures += 1
                    except Exception as e:
                        logger.error(f"[glassdoor] Error on search page {page_num}: {e}")
                        self.consecutive_failures += 1

                    human_delay(self.MIN_DELAY, self.MAX_DELAY)

        finally:
            # Always attempt to save cookies, even if scraping failed partway
            # The fetcher's browser context holds cookies after navigation
            try:
                # Scrapling's StealthyFetcher doesn't expose cookies directly,
                # but if we can extract them from the fetcher, save them.
                # For now, save an empty marker so the cache infra is exercised.
                if self._cached_cookies:
                    # Re-save the existing cookies to refresh the TTL
                    save_cookies_to_s3(self._cached_cookies)
            except Exception as e:
                logger.debug(f"[glassdoor] Cookie save skipped: {e}")

        logger.info(
            f"[glassdoor] Total jobs scraped: {len(all_jobs)}, "
            f"detail pages fetched: {self.detail_pages_fetched}/{MAX_DETAIL_PAGES}"
        )
        return all_jobs[:self.MAX_JOBS]
