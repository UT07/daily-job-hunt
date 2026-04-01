"""LinkedIn job scraper using Scrapling StealthyFetcher.

Scrapes public LinkedIn job search pages (no login required).
Uses residential proxy via StealthyFetcher for anti-detection.

Key defenses against LinkedIn blocking:
- Residential proxy rotation (PROXY_URL env var)
- Human-like delays (3-5s gaussian between page loads)
- Auth wall circuit breaker (3 redirects to login = stop)
- Volume cap (50 listings max per run)
- Description quality fallback (partial if detail page fails)
"""

import logging
import re
import urllib.parse

from scrapling import StealthyFetcher

from .base import BaseScraper, human_delay, normalize_text

logger = logging.getLogger(__name__)

# CSS selectors for job description on detail pages.
# LinkedIn A/B tests layouts — try in order of specificity.
_DESCRIPTION_SELECTORS = [
    ".description__text",
    ".show-more-less-html__markup",
    "[class*='description__text']",
    ".jobs-description__content",
    ".jobs-box__html-content",
    "[class*='jobs-description']",
    "article",
]

# CSS selectors for job cards on search results page.
_CARD_SELECTORS = [
    ".base-card",
    ".job-search-card",
    ".base-search-card",
    "[data-entity-urn]",
    "li.jobs-search__results-list > li",
]

# LinkedIn geoId mapping
_GEO_IDS = {
    "ireland": "104738515",
    "dublin": "104738515",
    "uk": "101165590",
    "london": "101165590",
    "england": "101165590",
    "us": "103644278",
    "united states": "103644278",
}


def _extract_job_id(url: str) -> str | None:
    """Extract LinkedIn job ID from a URL like /jobs/view/1234567890/."""
    match = re.search(r"/jobs/view/(\d+)", url)
    return match.group(1) if match else None


def _is_auth_wall(page_text: str) -> bool:
    """Check if page content indicates a LinkedIn auth wall / login redirect."""
    lower = page_text.lower()
    return any(signal in lower for signal in [
        "authwall",
        "sign in to linkedin",
        "join linkedin",
        "login",
        "/uas/login",
        "session_redirect",
    ])


class LinkedInScraper(BaseScraper):
    """Scrapes LinkedIn public job search using Scrapling StealthyFetcher.

    Does NOT require login. Uses public job search at
    linkedin.com/jobs/search?keywords={query}&location={location}.

    Features:
    - Residential proxy support via PROXY_URL env var
    - Auth wall circuit breaker (3 hits = stop)
    - Volume cap of 50 jobs per run
    - Partial description fallback when detail page fails
    - Human-like random delays between requests
    """

    SOURCE = "linkedin"
    MAX_JOBS = 50
    MIN_DELAY = 3.0
    MAX_DELAY = 5.0

    # Auth wall circuit breaker
    AUTH_WALL_THRESHOLD = 3

    def __init__(self):
        super().__init__()
        self._auth_wall_count = 0
        self._fetcher = None

    def _get_fetcher(self) -> StealthyFetcher:
        """Lazy-init the StealthyFetcher with proxy if configured."""
        if self._fetcher is None:
            self._fetcher = StealthyFetcher()
        return self._fetcher

    def _fetch_page(self, url: str) -> object | None:
        """Fetch a page using StealthyFetcher with optional proxy.

        Returns the Scrapling response object, or None if the request
        failed or hit the auth wall.
        """
        fetcher = self._get_fetcher()
        kwargs = {"url": url, "headless": True}
        if self.proxy_url:
            kwargs["proxy"] = {"server": self.proxy_url}

        try:
            response = fetcher.fetch(**kwargs)
        except Exception as e:
            logger.warning(f"[{self.SOURCE}] Fetch failed for {url}: {e}")
            return None

        if response is None or response.status != 200:
            status = response.status if response else "no response"
            logger.warning(f"[{self.SOURCE}] Non-200 status ({status}) for {url}")
            return None

        # Check for auth wall
        page_text = response.text or ""
        if _is_auth_wall(page_text):
            self._auth_wall_count += 1
            logger.warning(
                f"[{self.SOURCE}] Auth wall detected "
                f"({self._auth_wall_count}/{self.AUTH_WALL_THRESHOLD})"
            )
            return None

        return response

    def _auth_wall_tripped(self) -> bool:
        """Check if auth wall circuit breaker has been tripped."""
        return self._auth_wall_count >= self.AUTH_WALL_THRESHOLD

    def _build_search_url(self, query: str, location: str, start: int = 0) -> str:
        """Build LinkedIn public job search URL."""
        # Resolve geoId from location string
        geo_id = ""
        loc_lower = location.lower()
        for key, gid in _GEO_IDS.items():
            if key in loc_lower:
                geo_id = gid
                break
        if not geo_id:
            # Default to Ireland
            geo_id = "104738515"

        params = {
            "keywords": query,
            "location": location,
            "geoId": geo_id,
            "f_TPR": "r86400",          # Last 24 hours
            "f_E": "1,2,3",             # Internship + Entry + Associate
            "sortBy": "DD",             # Date descending
            "start": str(start),
        }

        # Add remote filter if location suggests remote
        if any(w in loc_lower for w in ["remote", "worldwide", "global"]):
            params["f_WT"] = "2"
            params.pop("geoId", None)

        return f"https://www.linkedin.com/jobs/search/?{urllib.parse.urlencode(params)}"

    def _parse_cards(self, response) -> list[dict]:
        """Extract job card data from a search results page response."""
        cards = []

        # Try each card selector until we find results
        elements = []
        for selector in _CARD_SELECTORS:
            elements = response.css(selector)
            if elements:
                break

        if not elements:
            logger.info(f"[{self.SOURCE}] No job cards found on page")
            return cards

        for el in elements:
            try:
                card = self._parse_single_card(el)
                if card:
                    cards.append(card)
            except Exception as e:
                logger.debug(f"[{self.SOURCE}] Failed to parse card: {e}")
                continue

        return cards

    def _parse_single_card(self, el) -> dict | None:
        """Parse a single job card element into a dict."""
        # Title
        title_el = (
            el.css_first(".base-search-card__title")
            or el.css_first("[class*='job-search-card__title']")
            or el.css_first("h3")
        )
        title = title_el.text.strip() if title_el else None
        if not title:
            return None

        # Company
        company_el = (
            el.css_first("[class*='base-search-card__subtitle']")
            or el.css_first("h4")
            or el.css_first("[class*='company']")
        )
        company = company_el.text.strip() if company_el else "Unknown"

        # Location
        loc_el = (
            el.css_first("[class*='job-search-card__location']")
            or el.css_first("[class*='base-search-card__metadata']")
        )
        location = loc_el.text.strip() if loc_el else ""

        # Apply URL — extract from the card's link
        link_el = (
            el.css_first("a.base-card__full-link")
            or el.css_first("a[class*='base-card']")
            or el.css_first("a[href*='/jobs/view/']")
        )
        apply_url = ""
        job_id = ""
        if link_el:
            href = link_el.attrib.get("href", "")
            # Strip tracking params
            apply_url = href.split("?")[0] if "?" in href else href
            job_id = _extract_job_id(apply_url) or ""

        if not apply_url:
            return None

        # Snippet text (the short description shown on search page)
        snippet_el = el.css_first("[class*='base-search-card__snippet']")
        snippet = snippet_el.text.strip() if snippet_el else ""

        # Detect experience level from badge/metadata
        experience_level = None
        meta_els = el.css("[class*='search-card__badge']") or []
        for meta in meta_els:
            text = meta.text.lower()
            if "entry" in text:
                experience_level = "entry"
            elif "mid" in text:
                experience_level = "mid"
            elif "senior" in text:
                experience_level = "senior"
            elif "intern" in text:
                experience_level = "internship"

        # Detect job type from metadata
        job_type = None
        is_remote = any(
            w in (title + " " + location).lower()
            for w in ["remote", "work from home", "hybrid"]
        )
        if is_remote:
            job_type = "remote"

        return {
            "title": title,
            "company": company,
            "location": location,
            "apply_url": apply_url,
            "job_id": job_id,
            "description": snippet,
            "description_quality": "snippet",
            "experience_level": experience_level,
            "job_type": job_type,
        }

    def _fetch_job_description(self, job: dict) -> dict:
        """Fetch full job description from the detail page.

        Updates the job dict in-place with the full description and
        sets description_quality to "full" on success, or keeps "partial"
        if we got at least some content from the snippet.
        """
        job_id = job.get("job_id") or _extract_job_id(job.get("apply_url", ""))
        if not job_id:
            return job

        detail_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

        human_delay(self.MIN_DELAY, self.MAX_DELAY)

        response = self._fetch_page(detail_url)
        if response is None:
            # Keep whatever description we have from the snippet
            if job.get("description"):
                job["description_quality"] = "partial"
            return job

        # Try each description selector
        for selector in _DESCRIPTION_SELECTORS:
            desc_el = response.css_first(selector)
            if desc_el:
                raw_text = desc_el.text or ""
                cleaned = normalize_text(raw_text)
                if len(cleaned) > 100:
                    job["description"] = cleaned[:10000]
                    job["description_quality"] = "full"

                    # Try to extract experience level from description
                    if not job.get("experience_level"):
                        job["experience_level"] = self._infer_experience_level(cleaned)

                    # Try to extract job type from description
                    if not job.get("job_type"):
                        job["job_type"] = self._infer_job_type(cleaned)

                    return job

        # If none of the selectors matched, mark as partial
        if job.get("description"):
            job["description_quality"] = "partial"
        logger.debug(f"[{self.SOURCE}] Could not extract description for job {job_id}")
        return job

    @staticmethod
    def _infer_experience_level(description: str) -> str | None:
        """Infer experience level from description text."""
        lower = description.lower()
        if any(w in lower for w in ["entry level", "entry-level", "junior", "graduate", "new grad"]):
            return "entry"
        if any(w in lower for w in ["mid level", "mid-level", "intermediate"]):
            return "mid"
        if any(w in lower for w in ["senior", "staff", "principal", "lead"]):
            return "senior"
        if any(w in lower for w in ["intern ", "internship"]):
            return "internship"
        return None

    @staticmethod
    def _infer_job_type(description: str) -> str | None:
        """Infer job type from description text."""
        lower = description.lower()
        if any(w in lower for w in ["fully remote", "100% remote", "work from home"]):
            return "remote"
        if "hybrid" in lower:
            return "hybrid"
        if any(w in lower for w in ["on-site", "onsite", "in-office"]):
            return "onsite"
        if any(w in lower for w in ["contract", "contractor"]):
            return "contract"
        if any(w in lower for w in ["part-time", "part time"]):
            return "part-time"
        return None

    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape LinkedIn jobs for the given queries.

        For each query:
        1. Load the search results page (paginate up to MAX_JOBS)
        2. Parse job cards (title, company, location, apply_url, job_id)
        3. Fetch detail pages for full descriptions
        4. Return list of job dicts ready for _save_job()

        Respects:
        - Auth wall circuit breaker (3 redirects = stop entirely)
        - Volume cap (MAX_JOBS total across all queries)
        - Human-like delays between all page loads
        """
        import os
        location = os.environ.get("SCRAPE_LOCATION", "Ireland")

        all_jobs: list[dict] = []

        for query in queries:
            if self._auth_wall_tripped():
                logger.warning(
                    f"[{self.SOURCE}] Auth wall circuit breaker tripped "
                    f"({self._auth_wall_count}/{self.AUTH_WALL_THRESHOLD}) "
                    f"-- skipping remaining queries"
                )
                break

            if len(all_jobs) >= self.MAX_JOBS:
                logger.info(f"[{self.SOURCE}] Volume cap reached ({self.MAX_JOBS})")
                break

            logger.info(f"[{self.SOURCE}] Searching: query='{query}', location='{location}'")

            # --- Phase 1: Collect job cards from search pages ---
            query_jobs = self._scrape_search_pages(query, location)

            if not query_jobs:
                logger.info(f"[{self.SOURCE}] No jobs found for query '{query}'")
                continue

            logger.info(f"[{self.SOURCE}] Found {len(query_jobs)} cards for '{query}'")

            # --- Phase 2: Fetch full descriptions ---
            remaining_slots = self.MAX_JOBS - len(all_jobs)
            jobs_to_process = query_jobs[:remaining_slots]
            enriched = self._enrich_descriptions(jobs_to_process)

            all_jobs.extend(enriched)
            logger.info(
                f"[{self.SOURCE}] Query '{query}': {len(enriched)} jobs collected "
                f"(total: {len(all_jobs)})"
            )

        logger.info(
            f"[{self.SOURCE}] Scrape complete: {len(all_jobs)} jobs total, "
            f"auth_walls={self._auth_wall_count}"
        )
        return all_jobs

    def _scrape_search_pages(self, query: str, location: str) -> list[dict]:
        """Scrape search result pages for a single query, paginating."""
        query_jobs: list[dict] = []
        page_size = 25  # LinkedIn returns 25 results per page

        # Calculate how many pages we need
        max_pages = (self.MAX_JOBS // page_size) + 1

        for page_num in range(max_pages):
            if self._auth_wall_tripped():
                break

            if len(query_jobs) >= self.MAX_JOBS:
                break

            start = page_num * page_size
            url = self._build_search_url(query, location, start=start)

            logger.info(f"[{self.SOURCE}] Fetching search page {page_num + 1} (start={start})")

            human_delay(self.MIN_DELAY, self.MAX_DELAY)

            response = self._fetch_page(url)
            if response is None:
                logger.warning(
                    f"[{self.SOURCE}] Search page {page_num + 1} failed, stopping pagination"
                )
                self.consecutive_failures += 1
                break

            # Reset consecutive failures on successful page load
            self.consecutive_failures = 0

            cards = self._parse_cards(response)
            if not cards:
                logger.info(
                    f"[{self.SOURCE}] No more cards on page {page_num + 1}, stopping pagination"
                )
                break

            query_jobs.extend(cards)
            logger.info(
                f"[{self.SOURCE}] Page {page_num + 1}: {len(cards)} cards "
                f"(running total: {len(query_jobs)})"
            )

        return query_jobs

    def _enrich_descriptions(self, jobs: list[dict]) -> list[dict]:
        """Fetch full descriptions for a list of jobs.

        Stops early if auth wall circuit breaker trips.
        Jobs that fail get description_quality="partial".
        """
        enriched = []
        full_count = 0
        partial_count = 0

        for i, job in enumerate(jobs):
            if self._auth_wall_tripped():
                # Keep remaining jobs with whatever description they have
                for remaining in jobs[i:]:
                    if remaining.get("description"):
                        remaining["description_quality"] = "partial"
                        partial_count += 1
                    enriched.append(remaining)
                break

            job = self._fetch_job_description(job)

            if job.get("description_quality") == "full":
                full_count += 1
            else:
                partial_count += 1

            enriched.append(job)

        logger.info(
            f"[{self.SOURCE}] Description enrichment: "
            f"{full_count} full, {partial_count} partial"
        )
        return enriched


# Entry point for standalone execution (e.g., in Fargate container)
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scraper = LinkedInScraper()
    try:
        scraper.run()
    except Exception as e:
        logger.error(f"LinkedIn scraper failed: {e}", exc_info=True)
        sys.exit(1)
