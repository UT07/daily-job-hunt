"""Irish job portals scraper: Jobs.ie + IrishJobs + GradIreland.

These are simple Irish job sites with minimal anti-bot protection,
so we use Scrapling's basic Fetcher (no proxy needed).

All three sites are scraped in a single run. Standard CSS selector
extraction with follow-through to apply URLs for full job descriptions.

Volume cap: 50 per source (150 total).
"""

import logging
import urllib.parse

from scrapers.playwright.base import BaseScraper, human_delay

logger = logging.getLogger(__name__)


# ======================================================================
# Site-specific configurations
# ======================================================================

SITES = {
    "jobs_ie": {
        "name": "Jobs.ie",
        "base_url": "https://www.jobs.ie",
        "search_path": "/SearchResults.aspx",
        "search_params": lambda q, loc: {
            "Keywords": q,
            "Location": loc,
            "Category": "",
            "Recruiter": "",
            "SortBy": "MostRecent",
        },
        "max_jobs": 50,
        # CSS selectors
        "card_selector": "div.result",
        "title_selector": "a.result-anchor h2, a.result-anchor .title, h2.result-title a",
        "company_selector": "h3.result-company, .result-company-name, span.company",
        "location_selector": "li.location, span.result-location, .location",
        "link_selector": "a.result-anchor, h2.result-title a",
        "salary_selector": "li.salary, span.result-salary",
        # Detail page description selector
        "detail_desc_selector": "div.job-description, div#job-description, div.jobDescription, div.job-detail-description",
    },
    "irishjobs": {
        "name": "IrishJobs",
        "base_url": "https://www.irishjobs.ie",
        "search_path": "/ShowResults.aspx",
        "search_params": lambda q, loc: {
            "Keywords": q,
            "Location": loc,
            "Category": "",
            "SortBy": "MostRecent",
        },
        "max_jobs": 50,
        # CSS selectors
        "card_selector": "div.job-result, div.job, li.search-result",
        "title_selector": "a.job-result-title h2, a.job-title, h2 a",
        "company_selector": "h3.job-result-company, span.company-name, .company",
        "location_selector": "li.location, span.job-location, .location",
        "link_selector": "a.job-result-title, a.job-title, h2 a",
        "salary_selector": "li.salary, span.salary",
        "detail_desc_selector": "div.job-description, div.job-details-desc, div#JobDescription",
    },
    "gradireland": {
        "name": "GradIreland",
        "base_url": "https://gradireland.com",
        "search_path": "/jobs",
        "search_params": lambda q, loc: {
            "keywords": q,
            "location": loc,
            "sort": "date",
        },
        "max_jobs": 50,
        # CSS selectors
        "card_selector": "div.job-result, article.job-card, div.search-result, li.job-item",
        "title_selector": "h2 a, h3 a, a.job-title, .job-card-title a",
        "company_selector": "span.company, div.company-name, .job-card-company",
        "location_selector": "span.location, div.job-location, .job-card-location",
        "link_selector": "h2 a, h3 a, a.job-title, .job-card-title a",
        "salary_selector": "span.salary, div.salary",
        "detail_desc_selector": "div.job-description, div.job-detail, div.vacancy-description, article.job-description",
    },
}


class IrishPortalsScraper(BaseScraper):
    """Scrapes Jobs.ie, IrishJobs.ie, and GradIreland in a single run.

    Uses Scrapling's basic Fetcher -- these sites have minimal anti-bot
    protection, so no proxy or stealth browser is needed.

    Strategy per site:
    1. Fetch search results pages.
    2. Parse job cards with CSS selectors.
    3. Follow apply/detail URLs for full job descriptions.
    """

    SOURCE = "irish_portals"
    MAX_JOBS = 150  # 50 per source
    MIN_DELAY = 1.5
    MAX_DELAY = 3.0
    MAX_CONSECUTIVE_FAILURES = 3
    USE_PROXY = False

    def __init__(self):
        super().__init__()
        self.max_pages_per_site = 3

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------
    def _build_search_url(self, site: dict, query: str, location: str, page: int = 1) -> str:
        params = site["search_params"](query, location)
        if page > 1:
            params["page"] = page
        return f"{site['base_url']}{site['search_path']}?{urllib.parse.urlencode(params)}"

    # ------------------------------------------------------------------
    # Job card extraction from search results
    # ------------------------------------------------------------------
    def _extract_cards(self, page, site: dict) -> list[dict]:
        """Extract job card metadata from a search results page."""
        cards_data = []

        cards = None
        for selector in site["card_selector"].split(", "):
            cards = page.css(selector.strip())
            if cards:
                break

        if not cards:
            logger.warning(f"[{site['name']}] No job cards found")
            return cards_data

        for card in cards:
            try:
                # Title
                title_el = None
                for sel in site["title_selector"].split(", "):
                    title_el = card.css_first(sel.strip())
                    if title_el:
                        break
                title = title_el.text.strip() if title_el else ""

                # Company
                company_el = None
                for sel in site["company_selector"].split(", "):
                    company_el = card.css_first(sel.strip())
                    if company_el:
                        break
                company = company_el.text.strip() if company_el else ""

                # Location
                loc_el = None
                for sel in site["location_selector"].split(", "):
                    loc_el = card.css_first(sel.strip())
                    if loc_el:
                        break
                location = loc_el.text.strip() if loc_el else ""

                # Detail link
                link_el = None
                for sel in site["link_selector"].split(", "):
                    link_el = card.css_first(sel.strip())
                    if link_el:
                        break
                detail_url = ""
                if link_el:
                    href = link_el.attrib.get("href", "")
                    if href:
                        detail_url = href if href.startswith("http") else f"{site['base_url']}{href}"

                # Salary (optional)
                salary = None
                for sel in site["salary_selector"].split(", "):
                    salary_el = card.css_first(sel.strip())
                    if salary_el:
                        salary = salary_el.text.strip()
                        break

                if title:
                    cards_data.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "detail_url": detail_url,
                        "salary": salary,
                    })
            except Exception as e:
                logger.debug(f"[{site['name']}] Failed to parse card: {e}")
                continue

        return cards_data

    # ------------------------------------------------------------------
    # Detail page extraction
    # ------------------------------------------------------------------
    def _fetch_detail(self, fetcher, site: dict, job_meta: dict) -> dict | None:
        """Fetch a detail page and extract the full job description."""
        url = job_meta.get("detail_url")
        if not url:
            # Return what we have with snippet quality
            return {
                "title": job_meta["title"],
                "company": job_meta["company"],
                "location": job_meta["location"],
                "description": "",
                "apply_url": "",
                "salary": job_meta.get("salary"),
                "job_type": None,
                "description_quality": "title_only",
            }

        try:
            page = fetcher.fetch(url)

            if not page or not page.status == 200:
                logger.debug(f"[{site['name']}] Bad detail response for {url}")
                return None

            # Extract full description
            desc_el = None
            for sel in site["detail_desc_selector"].split(", "):
                desc_el = page.css_first(sel.strip())
                if desc_el:
                    break

            description = ""
            if desc_el:
                # Get text content, normalizing whitespace
                description = desc_el.text.strip()

            # Even without the primary selector, try the main content area
            if not description:
                # Try broader selectors
                fallback_el = (
                    page.css_first("main")
                    or page.css_first("article")
                    or page.css_first("div#content")
                    or page.css_first("div.content")
                )
                if fallback_el:
                    description = fallback_el.text.strip()

            quality = "full" if len(description) > 200 else "snippet" if description else "title_only"

            return {
                "title": job_meta["title"],
                "company": job_meta["company"],
                "location": job_meta["location"],
                "description": description,
                "apply_url": url,
                "salary": job_meta.get("salary"),
                "job_type": None,
                "description_quality": quality,
            }

        except Exception as e:
            logger.debug(f"[{site['name']}] Error fetching detail {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Scrape a single site
    # ------------------------------------------------------------------
    def _scrape_site(self, fetcher, site_key: str, queries: list[str], location: str) -> list[dict]:
        """Scrape a single Irish job site."""
        site = SITES[site_key]
        site_jobs = []
        site_max = site["max_jobs"]

        # Reset consecutive failures per site
        site_failures = 0

        for query in queries:
            if len(site_jobs) >= site_max:
                break

            for page_num in range(1, self.max_pages_per_site + 1):
                if len(site_jobs) >= site_max or site_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    break

                url = self._build_search_url(site, query, location, page_num)
                logger.info(f"[{site['name']}] Fetching page {page_num} for '{query}': {url}")

                try:
                    page = fetcher.fetch(url)

                    if not page or not page.status == 200:
                        status = page.status if page else "no response"
                        logger.warning(f"[{site['name']}] Bad response: {status}")
                        site_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    cards = self._extract_cards(page, site)

                    if not cards:
                        site_failures += 1
                        human_delay(self.MIN_DELAY, self.MAX_DELAY)
                        continue

                    site_failures = 0

                    # Follow each card's detail URL for full JD
                    for card_meta in cards:
                        if len(site_jobs) >= site_max:
                            break

                        human_delay(self.MIN_DELAY, self.MAX_DELAY)

                        job = self._fetch_detail(fetcher, site, card_meta)
                        if job and job.get("title") and job.get("company"):
                            site_jobs.append(job)
                            logger.info(f"[{site['name']}] Got: {job['title']} at {job['company']}")
                        else:
                            site_failures += 1

                except Exception as e:
                    logger.error(f"[{site['name']}] Error on page {page_num}: {e}")
                    site_failures += 1

                human_delay(self.MIN_DELAY, self.MAX_DELAY)

        logger.info(f"[{site['name']}] Scraped {len(site_jobs)} jobs")
        return site_jobs

    # ------------------------------------------------------------------
    # Main scrape method
    # ------------------------------------------------------------------
    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape all three Irish job portals.

        Iterates over Jobs.ie, IrishJobs, and GradIreland,
        extracting up to 50 jobs per site (150 total).
        """
        from scrapling import Fetcher

        location = __import__("os").environ.get("SCRAPE_LOCATION", "Ireland")
        all_jobs = []

        fetcher = Fetcher()

        for site_key in SITES:
            if self._circuit_break():
                logger.warning("[irish_portals] Circuit breaker tripped, skipping remaining sites")
                break

            logger.info(f"[irish_portals] Starting scrape of {SITES[site_key]['name']}")
            site_jobs = self._scrape_site(fetcher, site_key, queries, location)
            all_jobs.extend(site_jobs)

        logger.info(f"[irish_portals] Total jobs across all portals: {len(all_jobs)}")
        return all_jobs
