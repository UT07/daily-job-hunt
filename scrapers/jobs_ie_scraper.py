"""Jobs.ie scraper - major Irish job board.

Uses requests-based HTML parsing (lightweight, no browser needed).
Jobs.ie URL pattern: https://www.jobs.ie/jobs?query={query}&location={location}
"""

from __future__ import annotations
import logging
import re
import requests
import urllib.parse
from typing import List
from .base import BaseScraper, Job

logger = logging.getLogger(__name__)

# Shared headers to reduce bot detection
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.jobs.ie/",
    "DNT": "1",
    "Connection": "keep-alive",
}


class JobsIeScraper(BaseScraper):
    """Scrapes Jobs.ie -- Ireland's leading job site.

    Uses lightweight requests + regex parsing. No Playwright dependency.
    """

    name = "jobs_ie"
    BASE_URL = "https://www.jobs.ie"
    _MAX_RETRIES = 2  # Don't retry endlessly on an unresponsive site
    _TIMEOUT = 15     # Seconds -- reduced from 30 to fail fast

    def __init__(self, max_pages: int = 2):
        self.max_pages = max_pages

    _site_reachable: bool | None = None  # Class-level: skip all queries if site is down
    _health_checked: bool = False  # Only run health check once per process

    def _health_check(self) -> bool:
        """Quick HEAD request to verify the site is reachable. Run once per process."""
        if JobsIeScraper._health_checked:
            return JobsIeScraper._site_reachable is not False
        JobsIeScraper._health_checked = True
        try:
            resp = requests.head(self.BASE_URL, headers=_HEADERS, timeout=5, allow_redirects=True)
            if resp.status_code < 500:
                JobsIeScraper._site_reachable = True
                return True
        except requests.RequestException:
            pass
        logger.warning("[Jobs.ie] Site unreachable (health check failed) -- skipping all queries this run")
        JobsIeScraper._site_reachable = False
        return False

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        """Search Jobs.ie and return normalized Job objects."""
        # Fast bail: if we already know the site is unreachable, skip immediately
        if JobsIeScraper._site_reachable is False:
            return []

        # One-time health check before first real request
        if not self._health_check():
            return []

        jobs: List[Job] = []
        try:
            jobs = self._search_requests(query, location, days_back)
            if jobs:
                JobsIeScraper._site_reachable = True
        except requests.exceptions.Timeout:
            logger.warning(f"[Jobs.ie] Timeout for '{query}' -- marking site unreachable for this run")
            JobsIeScraper._site_reachable = False
            return []
        except Exception as e:
            logger.error(f"[Jobs.ie] Error searching '{query}' in '{location}': {e}")
            return []

        unique = self.deduplicate(jobs)
        if unique:
            logger.info(f"[Jobs.ie] '{query}' in '{location}' -> {len(unique)} jobs")
        return unique

    def _search_requests(self, query: str, location: str, days_back: int) -> List[Job]:
        """Fetch and parse job listings from Jobs.ie search results."""
        jobs: List[Job] = []

        for page in range(1, self.max_pages + 1):
            params: dict = {
                "query": query,
                "page": page,
            }
            if location and location.lower() not in ("ireland", "remote", ""):
                params["location"] = location.replace(", Ireland", "").strip()

            url = f"{self.BASE_URL}/jobs?{urllib.parse.urlencode(params)}"

            try:
                resp = requests.get(
                    url, headers=_HEADERS, timeout=self._TIMEOUT,
                )
                if resp.status_code == 429:
                    logger.warning("[Jobs.ie] Rate limited -- stopping pagination")
                    break
                if resp.status_code != 200:
                    logger.warning(f"[Jobs.ie] HTTP {resp.status_code} for page {page}")
                    break

                html = resp.text

                # Detect bot protection / challenge pages
                if "captcha" in html.lower() or "challenge" in html.lower()[:500]:
                    logger.warning("[Jobs.ie] Blocked by bot protection")
                    break

                page_jobs = self._parse_listings(html, location)
                if not page_jobs:
                    break  # No more results
                jobs.extend(page_jobs)

            except requests.exceptions.Timeout:
                # Fast fail: first timeout on any page means site is down
                logger.warning(f"[Jobs.ie] Timeout on page {page} -- skipping remaining pages")
                raise  # Propagate to search() which marks site unreachable
            except requests.RequestException as e:
                logger.error(f"[Jobs.ie] Request failed (page {page}): {e}")
                break

        return jobs

    def _parse_listings(self, html: str, fallback_location: str) -> List[Job]:
        """Extract job listings from the HTML response.

        Jobs.ie uses structured job cards. We try multiple regex patterns
        to handle layout variations.
        """
        jobs: List[Job] = []

        # --- Strategy 1: JSON-LD structured data (most reliable if present) ---
        json_ld_blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in json_ld_blocks:
            try:
                import json
                data = json.loads(block)
                # Could be a single JobPosting or a list
                postings = data if isinstance(data, list) else [data]
                for item in postings:
                    if item.get("@type") != "JobPosting":
                        continue
                    title = item.get("title", "").strip()
                    if not title or len(title) < 3:
                        continue

                    company = ""
                    org = item.get("hiringOrganization", {})
                    if isinstance(org, dict):
                        company = org.get("name", "")

                    loc_obj = item.get("jobLocation", {})
                    loc = ""
                    if isinstance(loc_obj, dict):
                        addr = loc_obj.get("address", {})
                        if isinstance(addr, dict):
                            loc = addr.get("addressLocality", "")
                    elif isinstance(loc_obj, list) and loc_obj:
                        first = loc_obj[0]
                        if isinstance(first, dict):
                            addr = first.get("address", {})
                            if isinstance(addr, dict):
                                loc = addr.get("addressLocality", "")

                    salary = ""
                    sal_obj = item.get("baseSalary", {})
                    if isinstance(sal_obj, dict):
                        val = sal_obj.get("value", {})
                        if isinstance(val, dict):
                            min_val = val.get("minValue", "")
                            max_val = val.get("maxValue", "")
                            currency = sal_obj.get("currency", "EUR")
                            sym = "EUR" if currency == "EUR" else currency
                            if min_val and max_val:
                                salary = f"{sym} {min_val} - {max_val}"
                            elif min_val:
                                salary = f"{sym} {min_val}+"

                    apply_url = item.get("url", "") or item.get("sameAs", "")
                    posted = item.get("datePosted", "")

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc or fallback_location or "Ireland",
                        description=item.get("description", "")[:500],
                        apply_url=apply_url,
                        source="jobs_ie",
                        posted_date=posted,
                        salary=salary,
                        remote="remote" in (title + loc).lower(),
                    ))
            except (ValueError, KeyError, TypeError):
                continue

        if jobs:
            return jobs

        # --- Strategy 2: HTML regex patterns for job cards ---
        # Pattern: job title links with href to job detail pages
        # Jobs.ie typically uses paths like /job/... or /Jobs/...
        card_pattern = re.compile(
            r'<a[^>]+href="(/[Jj]ob[s]?/[^"]+)"[^>]*>\s*'
            r'(?:<[^>]+>)*\s*([^<]{5,100}?)\s*(?:</[^>]+>)*\s*</a>',
            re.DOTALL,
        )

        # Try to find company and location near each job link
        # Look for common patterns in job listing cards
        for match in card_pattern.finditer(html):
            href = match.group(1).strip()
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            title = re.sub(r'\s+', ' ', title)

            if not title or len(title) < 5:
                continue
            # Skip navigation/footer links
            if any(skip in title.lower() for skip in [
                "sign in", "register", "cookie", "privacy", "terms",
                "about us", "contact", "help", "faq", "browse",
            ]):
                continue

            full_url = self.BASE_URL + href if not href.startswith("http") else href

            # Try to extract company from surrounding HTML context
            company = self._extract_nearby(html, match.end(), "company")
            loc = self._extract_nearby(html, match.end(), "location")
            salary = self._extract_nearby(html, match.end(), "salary")
            posted = self._extract_nearby(html, match.end(), "date")

            jobs.append(Job(
                title=title,
                company=company,
                location=loc or fallback_location or "Ireland",
                description="",
                apply_url=full_url,
                source="jobs_ie",
                posted_date=posted,
                salary=salary,
                remote="remote" in (title + loc).lower(),
            ))

        return jobs

    def _extract_nearby(self, html: str, pos: int, field: str) -> str:
        """Try to extract a field value from HTML near the given position.

        Looks in the next ~1000 characters after the job title link
        for common CSS class/attribute patterns.
        """
        snippet = html[pos:pos + 1000]

        patterns = {
            "company": [
                r'class="[^"]*[Cc]ompany[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*employer[^"]*"[^>]*>\s*([^<]{2,80})',
                r'data-company="([^"]{2,80})"',
            ],
            "location": [
                r'class="[^"]*[Ll]ocation[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*place[^"]*"[^>]*>\s*([^<]{2,80})',
                r'data-location="([^"]{2,80})"',
            ],
            "salary": [
                r'class="[^"]*[Ss]alary[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*pay[^"]*"[^>]*>\s*([^<]{2,80})',
                r'(?:\u20ac|EUR)\s*[\d,.]+\s*[-\u2013]\s*(?:\u20ac|EUR)?\s*[\d,.]+',
            ],
            "date": [
                r'class="[^"]*[Dd]ate[^"]*"[^>]*>\s*([^<]{2,40})',
                r'class="[^"]*posted[^"]*"[^>]*>\s*([^<]{2,40})',
                r'datetime="([^"]{5,30})"',
            ],
        }

        for pat in patterns.get(field, []):
            m = re.search(pat, snippet)
            if m:
                return m.group(1).strip() if m.lastindex else m.group(0).strip()

        return ""
