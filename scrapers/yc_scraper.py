"""YCombinator job scrapers.

Two sources:
1. Work at a Startup (https://www.workatastartup.com/) — YC's official job board
2. Hacker News "Who's Hiring?" monthly threads — community postings

These are great sources for startup jobs, often more open to sponsorship
and remote work than traditional companies.
"""

from __future__ import annotations
import re
import json
import requests
import threading
from datetime import datetime, timedelta
from typing import List
from .base import BaseScraper, Job
from .browser import stealth_browser, run_async


class WorkAtAStartupScraper(BaseScraper):
    """Scrapes YC's Work at a Startup job board.

    Uses their internal API which returns JSON — no Playwright needed
    for the API path. Falls back to browser scraping if the API changes.
    """

    name = "yc_wats"
    API_URL = "https://www.workatastartup.com/companies/fetch"
    SEARCH_URL = "https://www.workatastartup.com/companies"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.workatastartup.com/companies",
        })

    def search(self, query: str, location: str = "", days_back: int = 7, **kwargs) -> List[Job]:
        jobs = []

        try:
            jobs = self._search_api(query, location, days_back)
        except Exception as e:
            print(f"  [YC-WATS] API failed ({e}), trying browser scrape...")
            jobs = run_async(self._search_browser(query, location))

        return self.deduplicate(jobs)

    def _search_api(self, query: str, location: str, days_back: int) -> List[Job]:
        """Try the internal WATS API."""
        jobs = []

        params = {
            "query": query,
            "page": 1,
        }

        loc_lower = location.lower()
        if "remote" in loc_lower:
            params["remote"] = "true"
        if any(w in loc_lower for w in ["ireland", "dublin", "europe", "eu"]):
            params["regions"] = "europe"

        q_lower = query.lower()
        if any(w in q_lower for w in ["sre", "devops", "infrastructure", "platform"]):
            params["role_types"] = "devops-infra"
        elif any(w in q_lower for w in ["fullstack", "full stack", "software engineer", "backend"]):
            params["role_types"] = "eng"

        resp = self.session.get(self.API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        companies = data if isinstance(data, list) else data.get("companies", data.get("results", []))

        for company in companies[:30]:
            company_name = company.get("name", "")
            company_url = company.get("website", "")
            batch = company.get("batch", "")

            for job_data in company.get("jobs", []):
                title = job_data.get("title", "")
                if not title:
                    continue

                combined = (title + " " + job_data.get("description", "")).lower()
                query_words = query.lower().split()
                if not any(w in combined for w in query_words):
                    continue

                job_url = f"https://www.workatastartup.com/jobs/{job_data.get('id', '')}"
                loc = job_data.get("pretty_location", "")
                is_remote = job_data.get("remote", False)

                salary = ""
                sal_min = job_data.get("salary_min")
                sal_max = job_data.get("salary_max")
                if sal_min and sal_max:
                    salary = f"${sal_min:,} - ${sal_max:,}"

                exp = job_data.get("experience_level", "")

                jobs.append(Job(
                    title=f"{title} ({batch})" if batch else title,
                    company=company_name,
                    location=loc or ("Remote" if is_remote else ""),
                    description=job_data.get("description", "")[:500],
                    apply_url=job_url or company_url,
                    source="yc_wats",
                    salary=salary,
                    experience_level=exp,
                    remote=is_remote or "remote" in loc.lower(),
                ))

        print(f"  [YC-WATS] Found {len(jobs)} jobs for '{query}'")
        return jobs

    async def _search_browser(self, query: str, location: str) -> List[Job]:
        """Fallback: scrape WATS with Playwright if API changes."""
        jobs = []
        url = f"{self.SEARCH_URL}?query={query}"

        async with stealth_browser() as browser:
            page = await browser.new_page()
            success = await browser.safe_goto(page, url)
            if not success:
                return jobs

            await browser.human_delay(3000, 5000)
            await browser.scroll_page(page, scrolls=5)

            cards = await page.query_selector_all('[class*="company-row"], [class*="job-listing"], .company-card')
            for card in cards:
                try:
                    title_el = await card.query_selector('[class*="job-name"], [class*="title"], h4')
                    company_el = await card.query_selector('[class*="company-name"], h2, h3')
                    link_el = await card.query_selector('a[href*="/jobs/"]')

                    title = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else ""
                    href = await link_el.get_attribute("href") if link_el else ""

                    if title:
                        if href and not href.startswith("http"):
                            href = "https://www.workatastartup.com" + href
                        jobs.append(Job(
                            title=title, company=company, location="",
                            description="", apply_url=href, source="yc_wats",
                        ))
                except Exception:
                    continue

        return jobs


class HackerNewsScraper(BaseScraper):
    """Scrapes Hacker News 'Who is Hiring?' monthly threads.

    IMPORTANT: The thread is fetched ONCE and cached. All queries filter
    against the cached data so we don't hammer the HN API with 70+ requests.
    """

    name = "hn_hiring"
    ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"

    # Class-level cache: fetch thread once, filter many times
    _cache_lock = threading.Lock()
    _cached_thread_id: str | None = None
    _cached_comments: list[dict] | None = None

    def search(self, query: str, location: str = "", days_back: int = 30, **kwargs) -> List[Job]:
        jobs = []

        try:
            # Fetch and cache the thread once across all queries
            self._ensure_cache()

            if self._cached_comments is None:
                return jobs

            jobs = self._filter_comments(query, location)
        except Exception as e:
            print(f"  [HN] Error: {e}")

        return self.deduplicate(jobs)

    def _ensure_cache(self):
        """Fetch the latest hiring thread once, cache for all queries."""
        with self._cache_lock:
            if self._cached_comments is not None:
                return  # Already cached

            thread_id = self._find_latest_thread()
            if not thread_id:
                print("  [HN] No recent 'Who is hiring?' thread found")
                self._cached_comments = []
                return

            self._cached_thread_id = thread_id

            resp = requests.get(
                f"https://hn.algolia.com/api/v1/items/{thread_id}",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            # Parse all comments into structured dicts once
            self._cached_comments = []
            for comment in data.get("children", [])[:300]:
                parsed = self._parse_comment(comment)
                if parsed:
                    self._cached_comments.append(parsed)

            print(f"  [HN] Cached {len(self._cached_comments)} job postings from thread {thread_id}")

    def _find_latest_thread(self) -> str | None:
        """Find the latest 'Ask HN: Who is hiring?' thread."""
        resp = requests.get(self.ALGOLIA_URL, params={
            "query": "Ask HN: Who is hiring?",
            "tags": "ask_hn",
            "hitsPerPage": 5,
        }, timeout=15)
        resp.raise_for_status()

        for hit in resp.json().get("hits", []):
            title = hit.get("title", "")
            if "who is hiring" in title.lower() and "freelancer" not in title.lower():
                return hit.get("objectID")
        return None

    def _parse_comment(self, comment: dict) -> dict | None:
        """Parse a single HN hiring comment into a structured dict."""
        text = comment.get("text", "")
        if not text or len(text) < 50:
            return None

        first_line = text.split("<p>")[0].split("\n")[0]
        first_line = re.sub(r"<[^>]+>", "", first_line).strip()

        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 2:
            return None

        company = parts[0] if parts else ""
        title_parts = []
        loc_parts = []
        is_remote = False
        url = ""

        for part in parts[1:]:
            p_lower = part.lower().strip()
            if "http" in p_lower:
                urls = re.findall(r'https?://\S+', part)
                url = urls[0] if urls else ""
            elif p_lower in ["remote", "onsite", "hybrid"] or "remote" in p_lower:
                is_remote = "remote" in p_lower
                if "onsite" not in p_lower:
                    loc_parts.append(part)
            elif any(geo in p_lower for geo in ["sf", "nyc", "london", "dublin", "berlin",
                     "ireland", "eu", "us", "uk", "remote", "worldwide",
                     "canada", "australia", "germany", "france", "india"]):
                loc_parts.append(part)
            else:
                title_parts.append(part)

        title = " | ".join(title_parts) if title_parts else first_line
        job_location = ", ".join(loc_parts) if loc_parts else ""

        full_text = re.sub(r"<[^>]+>", " ", text).strip()
        full_text = re.sub(r"\s+", " ", full_text)

        hn_url = f"https://news.ycombinator.com/item?id={comment.get('id', '')}"

        return {
            "title": title[:100],
            "company": company[:80],
            "location": job_location or ("Remote" if is_remote else "Unknown"),
            "description": full_text[:500],
            "apply_url": url or hn_url,
            "remote": is_remote,
            "searchable": (company + " " + title + " " + full_text).lower(),
        }

    def _filter_comments(self, query: str, location: str) -> List[Job]:
        """Filter cached comments by query and location. Strict matching."""
        jobs = []
        query_lower = query.lower()

        # Build strict keyword sets — require at least 2 matches for multi-word queries
        query_words = [w for w in query_lower.split() if len(w) > 2]
        loc_words = set()
        if location:
            loc_words = {w for w in location.lower().replace(",", "").split() if len(w) > 2}

        for entry in self._cached_comments:
            text = entry["searchable"]

            # Strict relevance: require the full query phrase OR 2+ individual words
            phrase_match = query_lower in text
            word_matches = sum(1 for w in query_words if w in text)

            if not phrase_match and word_matches < min(2, len(query_words)):
                continue

            # Location check
            if loc_words:
                entry_loc = entry["location"].lower() + " " + text
                is_remote_search = "remote" in location.lower()
                is_remote_job = entry["remote"]

                if is_remote_search:
                    if not is_remote_job:
                        continue
                else:
                    if not any(w in entry_loc for w in loc_words) and not is_remote_job:
                        continue

            jobs.append(Job(
                title=entry["title"],
                company=entry["company"],
                location=entry["location"],
                description=entry["description"],
                apply_url=entry["apply_url"],
                source="hn_hiring",
                remote=entry["remote"],
            ))

        return jobs
