"""Indeed scraper using Scrapling StealthyFetcher.

Extracts jobs from Indeed's hidden JSON payload
(window.mosaic.providerData["mosaic-provider-jobcards"]) which contains
full job descriptions, avoiding the need to navigate to detail pages.

Falls back to HTML card parsing if the JSON extraction fails.

Uses proxy + StealthyFetcher to handle Cloudflare Turnstile.
All fetches use timeout=30 to avoid Scrapling issue #100 hangs.
"""

import json
import logging
import re
import urllib.parse

from scrapers.playwright.base import BaseScraper, human_delay

logger = logging.getLogger(__name__)


class IndeedScraper(BaseScraper):
    """Scrapes Indeed job listings via Scrapling StealthyFetcher.

    Strategy:
    1. Load search results page with StealthyFetcher + proxy.
    2. Try to extract the hidden JSON blob that Indeed embeds in the page
       (window.mosaic.providerData["mosaic-provider-jobcards"]).
       This blob contains full job descriptions, saving detail-page fetches.
    3. If JSON extraction fails, fall back to parsing HTML job cards from
       the search results page (partial descriptions only).
    """

    SOURCE = "indeed"
    MAX_JOBS = 50
    MIN_DELAY = 2.0
    MAX_DELAY = 4.0
    USE_PROXY = True

    COUNTRY_DOMAINS = {
        "ie": "https://ie.indeed.com",
        "uk": "https://www.indeed.co.uk",
        "us": "https://www.indeed.com",
    }

    def __init__(self):
        super().__init__()
        self.country = "ie"
        self.max_pages = 3  # 15 results/page * 3 = ~45 jobs

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------
    def _build_search_url(self, query: str, location: str, start: int = 0) -> str:
        base = self.COUNTRY_DOMAINS.get(self.country, self.COUNTRY_DOMAINS["ie"])
        params = {
            "q": query,
            "l": location,
            "fromage": 3,  # last 3 days
            "sort": "date",
            "start": start,
        }
        return f"{base}/jobs?{urllib.parse.urlencode(params)}"

    # ------------------------------------------------------------------
    # JSON extraction (primary strategy)
    # ------------------------------------------------------------------
    def _extract_jobs_from_json(self, page_html: str) -> list[dict]:
        """Extract jobs from Indeed's embedded mosaic JSON blob.

        The blob lives in a <script> tag and contains full job descriptions,
        eliminating the need to visit individual job pages.
        """
        jobs = []

        # Pattern 1: window.mosaic.providerData["mosaic-provider-jobcards"]
        pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*({.+?});\s*$'
        match = re.search(pattern, page_html, re.MULTILINE | re.DOTALL)

        if not match:
            # Pattern 2: embedded in a script tag as JSON
            pattern2 = r'<script[^>]*>.*?mosaic-provider-jobcards.*?({.+?})\s*;?\s*</script>'
            match = re.search(pattern2, page_html, re.DOTALL)

        if not match:
            logger.debug("[indeed] No mosaic JSON blob found in page")
            return jobs

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.warning(f"[indeed] Failed to parse mosaic JSON: {e}")
            return jobs

        # Navigate the nested structure to find job cards
        results = []
        try:
            # Indeed nests results in metaData.mosaicProviderJobCardsModel.results
            model = data.get("metaData", {}).get("mosaicProviderJobCardsModel", {})
            results = model.get("results", [])
        except (AttributeError, TypeError):
            pass

        if not results:
            # Try alternate path: data.results directly
            results = data.get("results", [])

        for card in results:
            try:
                title = card.get("title", "") or card.get("displayTitle", "")
                company = card.get("company", "") or card.get("companyName", "")
                location = card.get("formattedLocation", "") or card.get("location", "")
                job_key = card.get("jobkey", "") or card.get("jk", "")

                # Full description from the JSON blob (the whole point)
                description = ""
                snippet = card.get("snippet", "")
                # Some versions embed full HTML description
                full_desc = card.get("jobDescription", "") or card.get("description", "")
                description = full_desc if full_desc else snippet

                # Build apply URL from job key
                base = self.COUNTRY_DOMAINS.get(self.country, self.COUNTRY_DOMAINS["ie"])
                apply_url = f"{base}/viewjob?jk={job_key}" if job_key else ""

                # Salary info
                salary = None
                sal_snippet = card.get("salarySnippet", {})
                if sal_snippet and sal_snippet.get("text"):
                    salary = sal_snippet["text"]
                elif card.get("extractedSalary"):
                    sal = card["extractedSalary"]
                    salary = f"{sal.get('min', '')}-{sal.get('max', '')} {sal.get('type', '')}"

                # Job type
                job_type = None
                for attr in card.get("jobTypes", []):
                    job_type = attr
                    break
                tax_attrs = card.get("taxonomyAttributes", [])
                for attr in tax_attrs:
                    if attr.get("label") == "job-types":
                        vals = attr.get("attributes", [])
                        if vals:
                            job_type = vals[0].get("label", job_type)

                if title and company:
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description,
                        "apply_url": apply_url,
                        "salary": salary,
                        "job_type": job_type,
                        "description_quality": "full" if full_desc else "snippet",
                    })
            except Exception as e:
                logger.debug(f"[indeed] Failed to parse JSON job card: {e}")
                continue

        logger.info(f"[indeed] Extracted {len(jobs)} jobs from JSON blob")
        return jobs

    # ------------------------------------------------------------------
    # HTML fallback extraction
    # ------------------------------------------------------------------
    def _extract_jobs_from_html(self, page) -> list[dict]:
        """Fall back to parsing HTML job cards when JSON extraction fails.

        Uses Scrapling's Adaptor (the page object) CSS selectors.
        Returns jobs with snippet-quality descriptions.
        """
        jobs = []

        # Indeed job cards are in <div class="job_seen_beacon"> or similar
        cards = page.css("div.job_seen_beacon") or page.css("div.jobsearch-ResultsList > div") or page.css("td.resultContent")

        if not cards:
            logger.warning("[indeed] No HTML job cards found on page")
            return jobs

        for card in cards:
            try:
                # Title
                title_el = card.css_first("h2.jobTitle a span") or card.css_first("h2.jobTitle span") or card.css_first("a[data-jk] span")
                title = title_el.text.strip() if title_el else ""

                # Company
                company_el = card.css_first("span[data-testid='company-name']") or card.css_first("span.companyName") or card.css_first("span.company")
                company = company_el.text.strip() if company_el else ""

                # Location
                loc_el = card.css_first("div[data-testid='text-location']") or card.css_first("div.companyLocation")
                location = loc_el.text.strip() if loc_el else ""

                # Apply URL
                link_el = card.css_first("a[data-jk]") or card.css_first("h2.jobTitle a")
                apply_url = ""
                if link_el:
                    jk = link_el.attrib.get("data-jk", "")
                    href = link_el.attrib.get("href", "")
                    if jk:
                        base = self.COUNTRY_DOMAINS.get(self.country, self.COUNTRY_DOMAINS["ie"])
                        apply_url = f"{base}/viewjob?jk={jk}"
                    elif href:
                        if href.startswith("/"):
                            base = self.COUNTRY_DOMAINS.get(self.country, self.COUNTRY_DOMAINS["ie"])
                            apply_url = f"{base}{href}"
                        else:
                            apply_url = href

                # Snippet (partial description)
                snippet_el = card.css_first("div.job-snippet") or card.css_first("div[class*='job-snippet']") or card.css_first("table.jobCardShelfContainer")
                snippet = snippet_el.text.strip() if snippet_el else ""

                # Salary
                salary_el = card.css_first("div.salary-snippet-container") or card.css_first("span.estimated-salary") or card.css_first("div[class*='salary']")
                salary = salary_el.text.strip() if salary_el else None

                if title and company:
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": snippet,
                        "apply_url": apply_url,
                        "salary": salary,
                        "job_type": None,
                        "description_quality": "snippet",
                    })
            except Exception as e:
                logger.debug(f"[indeed] Failed to parse HTML card: {e}")
                continue

        logger.info(f"[indeed] Extracted {len(jobs)} jobs from HTML cards (fallback)")
        return jobs

    # ------------------------------------------------------------------
    # Main scrape method
    # ------------------------------------------------------------------
    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape Indeed for the given queries.

        For each query/page:
        1. Fetch the search results page via StealthyFetcher + proxy.
        2. Try JSON extraction first (full descriptions).
        3. Fall back to HTML parsing (snippet descriptions).
        """
        from scrapling import StealthyFetcher

        location = __import__("os").environ.get("SCRAPE_LOCATION", "Ireland")
        all_jobs = []

        fetcher = StealthyFetcher()

        for query in queries:
            if len(all_jobs) >= self.MAX_JOBS:
                break

            for page_num in range(self.max_pages):
                if len(all_jobs) >= self.MAX_JOBS:
                    break

                if self._circuit_break():
                    logger.warning("[indeed] Circuit breaker tripped, stopping")
                    return all_jobs

                start = page_num * 15
                url = self._build_search_url(query, location, start)
                logger.info(f"[indeed] Fetching page {page_num + 1} for '{query}': {url}")

                try:
                    # timeout=30 to avoid Scrapling issue #100 Turnstile hang
                    page = fetcher.fetch(
                        url,
                        headless=True,
                        proxy={"server": self.proxy_url} if self.proxy_url else None,
                        timeout=30,
                    )

                    if not page or not page.status == 200:
                        status = page.status if page else "no response"
                        logger.warning(f"[indeed] Bad response: {status}")
                        self.consecutive_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    # Check for Cloudflare challenge page
                    page_text = page.text or ""
                    if "challenges.cloudflare.com" in page_text or "Just a moment" in page_text:
                        logger.warning("[indeed] Cloudflare challenge detected, skipping page")
                        self.consecutive_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    # Strategy 1: Extract from JSON blob (preferred)
                    jobs = self._extract_jobs_from_json(page_text)

                    # Strategy 2: Fall back to HTML parsing
                    if not jobs:
                        logger.info("[indeed] JSON extraction failed, trying HTML fallback")
                        jobs = self._extract_jobs_from_html(page)

                    if jobs:
                        all_jobs.extend(jobs)
                        self.consecutive_failures = 0
                        logger.info(f"[indeed] Got {len(jobs)} jobs from page {page_num + 1}")
                    else:
                        logger.warning(f"[indeed] No jobs found on page {page_num + 1}")
                        self.consecutive_failures += 1

                except Exception as e:
                    logger.error(f"[indeed] Error fetching page {page_num + 1}: {e}")
                    self.consecutive_failures += 1

                human_delay(self.MIN_DELAY, self.MAX_DELAY)

        logger.info(f"[indeed] Total jobs scraped: {len(all_jobs)}")
        return all_jobs[:self.MAX_JOBS]
