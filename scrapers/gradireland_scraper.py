"""GradIreland scraper - graduate roles in Ireland.

Uses requests-based HTML parsing (lightweight, no browser needed).
GradIreland is a Drupal-based site whose template changes occasionally.
This scraper uses multiple fallback strategies (JSON-LD, Drupal views-row,
article cards, generic link extraction) to stay resilient across template
changes.

Primary URL: https://gradireland.com/graduate-jobs?keyword={query}
Fallback URLs: /jobs?keywords={query}, /careers?keyword={query}
"""

from __future__ import annotations
import json
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

# URL path patterns to try — GradIreland has changed paths before
_SEARCH_PATHS = [
    ("/graduate-jobs", "keyword"),       # current known path
    ("/jobs", "keywords"),               # alternate Drupal views path
    ("/jobs", "keyword"),                # alternate with singular param
    ("/careers", "keyword"),             # another common Drupal path
    ("/graduate-jobs", "search_api_fulltext"),  # Drupal Search API param
]

# Non-job link text to filter out
_SKIP_TITLES = frozenset([
    "sign in", "register", "cookie", "privacy", "terms",
    "about", "contact", "help", "faq", "browse", "search",
    "all jobs", "view all", "read more", "learn more",
    "log in", "create account", "home", "menu", "navigation",
    "footer", "header", "skip to content", "back to top",
])


class GradIrelandScraper(BaseScraper):
    """Scrapes GradIreland -- graduate-focused Irish job board.

    Uses lightweight requests + regex parsing. No Playwright dependency.
    Good for entry-level / graduate roles in Ireland.

    Resilient against template changes via multiple extraction strategies:
    1. JSON-LD structured data (most reliable when present)
    2. Drupal views-row pattern (common Drupal listing structure)
    3. Article/card-based HTML extraction
    4. Generic job-link href extraction (last resort)
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
        else:
            logger.warning(
                f"[GradIreland] 0 jobs for '{query}' in '{location}' — "
                "template may have changed. Check HTML selectors."
            )
        return unique

    def _search_requests(self, query: str, location: str, days_back: int) -> List[Job]:
        """Fetch and parse job listings from GradIreland search results.

        Tries multiple URL path patterns. If the primary path returns 0 jobs,
        falls back to alternate paths before giving up.
        """
        jobs: List[Job] = []

        for path, param_name in _SEARCH_PATHS:
            page_jobs = self._try_search_path(path, param_name, query, location)
            if page_jobs:
                jobs.extend(page_jobs)
                break  # Found jobs on this path, no need to try others
            # Log and try next path
            logger.debug(f"[GradIreland] Path {path}?{param_name}= returned 0 jobs, trying next")

        return jobs

    def _try_search_path(
        self, path: str, param_name: str, query: str, location: str
    ) -> List[Job]:
        """Attempt to scrape jobs from a specific URL path pattern."""
        jobs: List[Job] = []

        for page in range(0, self.max_pages):
            params: dict = {param_name: query}
            if page > 0:
                params["page"] = page
            if location and location.lower() not in ("ireland", "remote", ""):
                params["location"] = location.replace(", Ireland", "").strip()

            url = f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}"

            try:
                resp = requests.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 429:
                    logger.warning("[GradIreland] Rate limited -- stopping pagination")
                    break
                if resp.status_code in (404, 403):
                    # This path doesn't exist or is blocked — try next path
                    logger.debug(f"[GradIreland] HTTP {resp.status_code} for {path}")
                    return []
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
                    if page == 0:
                        # First page returned 0 — this path may be wrong
                        return []
                    break  # Pagination exhausted
                jobs.extend(page_jobs)

            except requests.RequestException as e:
                logger.error(f"[GradIreland] Request failed (page {page}): {e}")
                break

        return jobs

    def _parse_listings(self, html: str, fallback_location: str) -> List[Job]:
        """Extract job listings from GradIreland HTML response.

        Tries multiple strategies in order of reliability:
        1. JSON-LD structured data
        2. Drupal views-row pattern
        3. Article/card-based extraction
        4. Generic href link matching
        5. Broad link extraction (last resort)
        """
        jobs: List[Job] = []

        # --- Strategy 1: JSON-LD structured data ---
        jobs = self._parse_json_ld(html, fallback_location)
        if jobs:
            return jobs

        # --- Strategy 2: Drupal views-row pattern ---
        jobs = self._parse_drupal_views(html, fallback_location)
        if jobs:
            return jobs

        # --- Strategy 3: Article / card-based extraction ---
        jobs = self._parse_article_cards(html, fallback_location)
        if jobs:
            return jobs

        # --- Strategy 4: Href-based job link extraction ---
        jobs = self._parse_job_links(html, fallback_location)
        if jobs:
            return jobs

        # --- Strategy 5: Broad link extraction (last resort) ---
        jobs = self._parse_generic_links(html, fallback_location)
        return jobs

    # ------------------------------------------------------------------
    # Strategy 1: JSON-LD structured data
    # ------------------------------------------------------------------
    def _parse_json_ld(self, html: str, fallback_location: str) -> List[Job]:
        """Extract jobs from JSON-LD JobPosting blocks."""
        jobs: List[Job] = []
        json_ld_blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in json_ld_blocks:
            try:
                data = json.loads(block)
                # Handle both single objects and arrays, and @graph wrappers
                if isinstance(data, dict) and "@graph" in data:
                    postings = data["@graph"]
                elif isinstance(data, list):
                    postings = data
                else:
                    postings = [data]

                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    if item.get("@type") not in ("JobPosting", "jobPosting"):
                        continue
                    title = (item.get("title") or item.get("name", "")).strip()
                    if not title or len(title) < 3:
                        continue

                    company = ""
                    org = item.get("hiringOrganization", {})
                    if isinstance(org, dict):
                        company = org.get("name", "")
                    elif isinstance(org, str):
                        company = org

                    loc = self._extract_json_ld_location(item)

                    apply_url = item.get("url", "") or item.get("sameAs", "")
                    posted = item.get("datePosted", "")
                    description = item.get("description", "") or ""

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=loc or fallback_location or "Ireland",
                        description=description[:2000],
                        apply_url=apply_url,
                        source="gradireland",
                        posted_date=posted,
                        remote="remote" in (title + loc).lower(),
                    ))
            except (ValueError, KeyError, TypeError):
                continue
        return jobs

    @staticmethod
    def _extract_json_ld_location(item: dict) -> str:
        """Extract location string from a JSON-LD JobPosting item."""
        loc_obj = item.get("jobLocation", {})
        if isinstance(loc_obj, dict):
            addr = loc_obj.get("address", {})
            if isinstance(addr, dict):
                parts = [
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                ]
                return ", ".join(p for p in parts if p) or ""
            if isinstance(addr, str):
                return addr
        elif isinstance(loc_obj, list) and loc_obj:
            first = loc_obj[0]
            if isinstance(first, dict):
                addr = first.get("address", {})
                if isinstance(addr, dict):
                    return addr.get("addressLocality", "")
        return ""

    # ------------------------------------------------------------------
    # Strategy 2: Drupal views-row pattern
    # ------------------------------------------------------------------
    def _parse_drupal_views(self, html: str, fallback_location: str) -> List[Job]:
        """Extract jobs from Drupal views-row / views-field structure.

        Drupal Views typically renders listings as:
            <div class="views-row">
              <div class="views-field views-field-title">
                <span class="field-content"><a href="...">Title</a></span>
              </div>
              <div class="views-field views-field-field-company">
                <span class="field-content">Company</span>
              </div>
              ...
            </div>
        """
        jobs: List[Job] = []

        # Split HTML by views-row boundaries
        rows = re.split(r'<div[^>]*class="[^"]*views-row[^"]*"', html)
        if len(rows) <= 1:
            # Also try Drupal 8+ field formatter classes
            rows = re.split(r'<div[^>]*class="[^"]*views-row[^"]*"', html)
            if len(rows) <= 1:
                return []

        for row_html in rows[1:]:  # Skip content before first views-row
            # Extract title + link
            title_match = re.search(
                r'class="[^"]*(?:views-field-title|field--name-title|views-field-name)[^"]*"'
                r'[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>\s*([^<]+)',
                row_html,
                re.DOTALL,
            )
            if not title_match:
                # Fallback: any <a> inside the row with a reasonable-looking href
                title_match = re.search(
                    r'<a[^>]+href="(/[^"]*(?:job|graduate|career|vacanc)[^"]*)"[^>]*>\s*([^<]{5,120})',
                    row_html,
                    re.DOTALL,
                )
            if not title_match:
                continue

            href = title_match.group(1).strip()
            title = re.sub(r'\s+', ' ', title_match.group(2).strip())
            if not title or len(title) < 5:
                continue
            if self._is_skip_title(title):
                continue

            full_url = self.BASE_URL + href if not href.startswith("http") else href

            # Extract company
            company = ""
            for comp_pat in [
                r'class="[^"]*(?:field--name-field-company|views-field-field-company|field-company)[^"]*"[^>]*>.*?<[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*(?:field--name-field-employer|views-field-field-employer|employer)[^"]*"[^>]*>.*?<[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*company[^"]*"[^>]*>\s*([^<]{2,80})',
            ]:
                m = re.search(comp_pat, row_html, re.DOTALL | re.IGNORECASE)
                if m:
                    company = m.group(1).strip()
                    break
            if not company:
                company = self._company_from_url(full_url)

            # Extract location
            loc = ""
            for loc_pat in [
                r'class="[^"]*(?:field--name-field-location|views-field-field-location|field-location)[^"]*"[^>]*>.*?<[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*location[^"]*"[^>]*>\s*([^<]{2,80})',
            ]:
                m = re.search(loc_pat, row_html, re.DOTALL | re.IGNORECASE)
                if m:
                    loc = m.group(1).strip()
                    break

            # Extract date
            posted = ""
            for date_pat in [
                r'class="[^"]*(?:field--name-field-date|views-field-field-date|date)[^"]*"[^>]*>.*?<[^>]*>\s*([^<]{2,40})',
                r'datetime="([^"]{5,30})"',
            ]:
                m = re.search(date_pat, row_html, re.DOTALL | re.IGNORECASE)
                if m:
                    posted = m.group(1).strip()
                    break

            jobs.append(Job(
                title=title,
                company=company,
                location=loc or fallback_location or "Ireland",
                description="",
                apply_url=full_url,
                source="gradireland",
                posted_date=posted,
                remote="remote" in (title + (loc or "")).lower(),
            ))

        return jobs

    # ------------------------------------------------------------------
    # Strategy 3: Article / card-based extraction
    # ------------------------------------------------------------------
    def _parse_article_cards(self, html: str, fallback_location: str) -> List[Job]:
        """Extract jobs from <article> or card-based HTML structures.

        Common patterns:
            <article class="job-card">
              <h2><a href="...">Title</a></h2>
              <span class="company">Company</span>
            </article>
        """
        jobs: List[Job] = []

        # Split by article tags or common card divs
        articles = re.split(
            r'<(?:article|div)[^>]*class="[^"]*(?:job-card|job-item|search-result|node--type-job|job-teaser|job-listing)[^"]*"',
            html,
            flags=re.IGNORECASE,
        )
        if len(articles) <= 1:
            return []

        for article_html in articles[1:]:
            # Truncate to avoid bleeding into next card
            end_marker = re.search(r'</article>|<article|<div[^>]*class="[^"]*(?:job-card|job-item|search-result)', article_html)
            if end_marker:
                article_html = article_html[:end_marker.start()]

            # Find title link
            title_match = re.search(
                r'<(?:h[1-6]|a)[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>\s*([^<]{5,120})',
                article_html,
                re.DOTALL,
            )
            if not title_match:
                title_match = re.search(
                    r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*(?:title|job-title)[^"]*"[^>]*>\s*([^<]{5,120})',
                    article_html,
                    re.DOTALL,
                )
            if not title_match:
                continue

            href = title_match.group(1).strip()
            title = re.sub(r'\s+', ' ', title_match.group(2).strip())
            if not title or len(title) < 5:
                continue
            if self._is_skip_title(title):
                continue

            full_url = self.BASE_URL + href if not href.startswith("http") else href

            company = self._extract_nearby(article_html, 0, "company")
            if not company:
                company = self._company_from_url(full_url)
            loc = self._extract_nearby(article_html, 0, "location")
            posted = self._extract_nearby(article_html, 0, "date")

            jobs.append(Job(
                title=title,
                company=company,
                location=loc or fallback_location or "Ireland",
                description="",
                apply_url=full_url,
                source="gradireland",
                posted_date=posted,
                remote="remote" in (title + (loc or "")).lower(),
            ))

        return jobs

    # ------------------------------------------------------------------
    # Strategy 4: Href-based job link extraction
    # ------------------------------------------------------------------
    def _parse_job_links(self, html: str, fallback_location: str) -> List[Job]:
        """Extract jobs by matching <a> tags with job-like href paths."""
        jobs: List[Job] = []

        # GradIreland uses /graduate-jobs/ or /job/ paths for individual listings
        card_pattern = re.compile(
            r'<a[^>]+href="(/(?:graduate-jobs|job|jobs|vacancy|opportunities|careers)/[^"]+)"[^>]*>\s*'
            r'(?:<[^>]+>)*\s*([^<]{5,120}?)\s*(?:</[^>]+>)*\s*</a>',
            re.DOTALL,
        )

        seen_hrefs: set = set()
        for match in card_pattern.finditer(html):
            href = match.group(1).strip()
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            title = re.sub(r'\s+', ' ', title)

            if not title or len(title) < 5:
                continue
            if self._is_skip_title(title):
                continue

            full_url = self.BASE_URL + href if not href.startswith("http") else href

            # Extract company and location from nearby HTML
            company = self._extract_nearby(html, match.end(), "company")
            if not company:
                company = self._company_from_url(full_url)
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
                remote="remote" in (title + (loc or "")).lower(),
            ))

        return jobs

    # ------------------------------------------------------------------
    # Strategy 5: Broad generic link extraction (last resort)
    # ------------------------------------------------------------------
    def _parse_generic_links(self, html: str, fallback_location: str) -> List[Job]:
        """Look for any links that look like job detail pages on gradireland.com."""
        jobs: List[Job] = []
        generic_pattern = re.compile(
            r'<a[^>]+href="((?:https?://(?:www\.)?gradireland\.com)?/[^"]*(?:job|vacanc|opportunit|career|graduate)[^"]*)"[^>]*>\s*'
            r'([^<]{5,120}?)\s*</a>',
            re.DOTALL | re.IGNORECASE,
        )
        seen_urls: set = set()
        for match in generic_pattern.finditer(html):
            url = match.group(1).strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = re.sub(r'\s+', ' ', match.group(2).strip())
            if not title or len(title) < 5:
                continue
            if self._is_skip_title(title):
                continue

            if not url.startswith("http"):
                url = self.BASE_URL + url
            company = self._company_from_url(url)
            jobs.append(Job(
                title=title,
                company=company,
                location=fallback_location or "Ireland",
                description="",
                apply_url=url,
                source="gradireland",
                remote="remote" in title.lower(),
            ))

        return jobs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_skip_title(title: str) -> bool:
        """Return True if the title looks like a navigation link, not a job."""
        lower = title.lower().strip()
        return lower in _SKIP_TITLES or any(skip in lower for skip in _SKIP_TITLES)

    def _company_from_url(self, url: str) -> str:
        """Try to infer company name from a GradIreland job URL.

        GradIreland URLs commonly follow the pattern:
            /graduate-jobs/{employer-slug}/{job-slug}
        or the full URL:
            https://gradireland.com/graduate-jobs/{employer-slug}/{job-slug}

        The segment immediately after the top-level path prefix is often the
        employer slug (hyphen-separated words). We convert it to title-case as
        a best-effort company name. Returns "" on any failure.
        """
        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.rstrip("/")
            parts = [p for p in path.split("/") if p]

            # Paths we recognise as job-listing prefixes whose next segment
            # is likely the employer slug
            _JOB_PREFIXES = {
                "graduate-jobs", "job", "jobs", "vacancy",
                "opportunities", "careers",
            }

            if len(parts) >= 2 and parts[0] in _JOB_PREFIXES:
                employer_slug = parts[1]
                # Skip single-segment paths like /jobs/12345 (numeric IDs)
                if not employer_slug.isdigit() and len(employer_slug) >= 2:
                    # Convert slug to readable name
                    name = re.sub(r"[-_]+", " ", employer_slug).strip()
                    # Discard slugs that look like job titles
                    _JOB_WORDS = {
                        "engineer", "developer", "analyst", "manager", "graduate",
                        "intern", "trainee", "associate", "officer", "specialist",
                        "consultant", "coordinator", "executive", "director",
                    }
                    words = name.lower().split()
                    if not any(w in _JOB_WORDS for w in words):
                        return name.title()
        except Exception:
            pass
        return ""

    def _extract_nearby(self, html: str, pos: int, field: str) -> str:
        """Try to extract a field value from HTML near the given position.

        Scans the next ~1500 chars after the job title for common patterns.
        """
        snippet = html[pos:pos + 1500]

        patterns = {
            "company": [
                # Drupal field classes
                r'class="[^"]*field--name-field-company[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
                r'class="[^"]*field--name-field-employer[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
                # Generic classes
                r'class="[^"]*[Cc]ompany[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*employer[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*org(?:anisation|anization)?[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*recruiter[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*brand[^"]*"[^>]*>\s*([^<]{2,80})',
                # Data attributes
                r'data-company="([^"]{2,80})"',
                r'data-employer="([^"]{2,80})"',
            ],
            "location": [
                r'class="[^"]*field--name-field-location[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,80})',
                r'class="[^"]*[Ll]ocation[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*place[^"]*"[^>]*>\s*([^<]{2,80})',
                r'class="[^"]*region[^"]*"[^>]*>\s*([^<]{2,80})',
                r'data-location="([^"]{2,80})"',
            ],
            "date": [
                r'class="[^"]*field--name-field-date[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,40})',
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
