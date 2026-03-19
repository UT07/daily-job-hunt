"""GradIreland scraper - graduate roles in Ireland.

Uses requests-based HTML parsing (lightweight, no browser needed).
GradIreland URL: https://gradireland.com/graduate-jobs?keyword={query}
"""

from __future__ import annotations
import logging
import re
import requests
import urllib.parse
from typing import List
from .base import BaseScraper, Job

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://gradireland.com/",
    "DNT": "1",
    "Connection": "keep-alive",
}


class GradIrelandScraper(BaseScraper):
    """Scrapes GradIreland — graduate-focused Irish job board.

    Uses lightweight requests + regex parsing. No Playwright dependency.
    Good for entry-level / graduate roles in Ireland.
    """

    name = "gradireland"
    BASE_URL = "https://gradireland.com"

    def __init__(self, max_pages: int = 2):
        self.max_pages = max_pages

    def search(self, query: str, location: str = "", days_back: int = 1, **kwargs) -> List[Job]:
        """Search GradIreland and return normalized Job objects."""
        jobs: List[Job] = []

        try:
            jobs = self._search_requests(query, location, days_back)
        except Exception as e:
            logger.error(f"[GradIreland] Error searching '{query}' in '{location}': {e}")
            return []

        unique = self.deduplicate(jobs)
        if unique:
            logger.info(f"[GradIreland] '{query}' in '{location}' -> {len(unique)} jobs")
        return unique

    def _search_requests(self, query: str, location: str, days_back: int) -> List[Job]:
        """Fetch and parse job listings from GradIreland search results."""
        jobs: List[Job] = []

        for page in range(0, self.max_pages):
            params: dict = {
                "keyword": query,
            }
            if page > 0:
                params["page"] = page
            if location and location.lower() not in ("ireland", "remote", ""):
                params["location"] = location.replace(", Ireland", "").strip()

            url = f"{self.BASE_URL}/graduate-jobs?{urllib.parse.urlencode(params)}"

            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                if resp.status_code == 429:
                    logger.warning("[GradIreland] Rate limited — stopping pagination")
                    break
                if resp.status_code != 200:
                    logger.warning(f"[GradIreland] HTTP {resp.status_code} for page {page}")
                    break

                html = resp.text

                # Detect bot protection
                if "captcha" in html.lower() or "access denied" in html.lower()[:500]:
                    logger.warning("[GradIreland] Blocked by bot protection")
                    break

                page_jobs = self._parse_listings(html, location)
                if not page_jobs:
                    break  # No more results
                jobs.extend(page_jobs)

            except requests.RequestException as e:
                logger.error(f"[GradIreland] Request failed (page {page}): {e}")
                break

        return jobs

    def _parse_listings(self, html: str, fallback_location: str) -> List[Job]:
        """Extract job listings from GradIreland HTML response.

        Tries JSON-LD structured data first, then falls back to HTML regex.
        """
        jobs: List[Job] = []

        # --- Strategy 1: JSON-LD structured data ---
        json_ld_blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in json_ld_blocks:
            try:
                import json
                data = json.loads(block)
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

                    loc = ""
                    loc_obj = item.get("jobLocation", {})
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

                    apply_url = item.get("url", "") or item.get("sameAs", "")
                    posted = item.get("datePosted", "")

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc or fallback_location or "Ireland",
                        description=item.get("description", "")[:500],
                        apply_url=apply_url,
                        source="gradireland",
                        posted_date=posted,
                        remote="remote" in (title + loc).lower(),
                    ))
            except (ValueError, KeyError, TypeError):
                continue

        if jobs:
            return jobs

        # --- Strategy 2: HTML regex for job cards ---
        # GradIreland uses /graduate-jobs/ or /job/ paths for individual listings
        card_pattern = re.compile(
            r'<a[^>]+href="(/(?:graduate-jobs|job|jobs|vacancy|opportunities)/[^"]+)"[^>]*>\s*'
            r'(?:<[^>]+>)*\s*([^<]{5,120}?)\s*(?:</[^>]+>)*\s*</a>',
            re.DOTALL,
        )

        for match in card_pattern.finditer(html):
            href = match.group(1).strip()
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            title = re.sub(r'\s+', ' ', title)

            if not title or len(title) < 5:
                continue
            # Skip non-job links
            if any(skip in title.lower() for skip in [
                "sign in", "register", "cookie", "privacy", "terms",
                "about", "contact", "help", "faq", "browse", "search",
                "all jobs", "view all", "read more", "learn more",
            ]):
                continue

            full_url = self.BASE_URL + href if not href.startswith("http") else href

            # Extract company and location from nearby HTML
            company = self._extract_nearby(html, match.end(), "company")
            loc = self._extract_nearby(html, match.end(), "location")
            posted = self._extract_nearby(html, match.end(), "date")

            jobs.append(Job(
                title=title,
                company=company,
                location=loc or fallback_location or "Ireland",
                description="",
                apply_url=full_url,
                source="gradireland",
                posted_date=posted,
                remote="remote" in (title + loc).lower(),
            ))

        # --- Strategy 3: Generic listing links as last resort ---
        if not jobs:
            # Look for any links that look like job detail pages
            generic_pattern = re.compile(
                r'<a[^>]+href="(https?://gradireland\.com/[^"]*(?:job|vacanc|opportunit)[^"]*)"[^>]*>\s*'
                r'([^<]{5,120}?)\s*</a>',
                re.DOTALL | re.IGNORECASE,
            )
            for match in generic_pattern.finditer(html):
                url = match.group(1).strip()
                title = match.group(2).strip()
                title = re.sub(r'\s+', ' ', title)
                if not title or len(title) < 5:
                    continue
                jobs.append(Job(
                    title=title,
                    company="",
                    location=fallback_location or "Ireland",
                    description="",
                    apply_url=url,
                    source="gradireland",
                    remote="remote" in title.lower(),
                ))

        return jobs

    def _extract_nearby(self, html: str, pos: int, field: str) -> str:
        """Try to extract a field value from HTML near the given position.

        Scans the next ~1000 chars after the job title for common patterns.
        """
        snippet = html[pos:pos + 1000]

        patterns = {
            "company": [
                r'class="[^"]*[Cc]ompany[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*employer[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*org[^"]*"[^>]*>\s*([^<]{2,80})',
                r'data-company="([^"]{2,80})"',
            ],
            "location": [
                r'class="[^"]*[Ll]ocation[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*place[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*region[^"]*"[^>]*>\s*([^<]{2,80})',
                r'data-location="([^"]{2,80})"',
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
