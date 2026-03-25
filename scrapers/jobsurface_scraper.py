"""JobSurface scraper — remote DevOps & Cloud jobs.

jobsurface.com sources hidden jobs from company career pages.
Uses Playwright with stealth mode due to Cloudflare Turnstile protection.
Site is a React SPA (Remix) — requires JavaScript rendering.
"""

from __future__ import annotations
import logging
import re
from typing import List
from .base import Job, BaseScraper
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)


class JobSurfaceScraper(BaseScraper):
    """Scraper for jobsurface.com remote DevOps/Cloud jobs."""

    name = "jobsurface"

    def __init__(self, max_pages: int = 1):
        self.max_pages = max_pages
        self.base_url = "https://www.jobsurface.com"

    def search(self, query: str, location: str = "", days_back: int = 7, **kwargs) -> List[Job]:
        """Search jobsurface.com for jobs matching the query."""
        return run_async(self._search_async(query, location, days_back))

    async def _search_async(self, query: str, location: str, days_back: int) -> List[Job]:
        jobs: List[Job] = []

        try:
            async with stealth_browser() as browser:
                page = await browser.new_page()

                # Navigate to jobs listing page
                search_url = f"{self.base_url}/jobs"
                logger.info(f"[{self.name}] Loading {search_url}")

                success = await browser.safe_goto(
                    page, search_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                if not success:
                    logger.warning(f"[{self.name}] Failed to load {search_url}")
                    return []

                # Wait for React hydration / SPA content to render
                await browser.human_delay(3000, 6000)
                await browser.scroll_page(page, scrolls=2)

                # Try to find and use a search input if available
                try:
                    search_input = await page.query_selector(
                        'input[type="search"], input[placeholder*="search" i], input[name="q"]'
                    )
                    if search_input:
                        await search_input.fill(query)
                        await search_input.press("Enter")
                        await browser.human_delay(2000, 4000)
                except Exception:
                    pass  # No search input found — browse what's shown

                # Extract job cards using multiple selector strategies
                selectors = [
                    'a[href*="/job/"]',
                    'a[href*="/jobs/"]',
                    '[class*="job-card"]',
                    '[class*="JobCard"]',
                    '[data-testid*="job"]',
                    'article',
                    '.job-listing',
                ]

                job_elements = []
                for selector in selectors:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        job_elements = elements
                        logger.info(f"[{self.name}] Found {len(elements)} elements with selector '{selector}'")
                        break

                if not job_elements:
                    # Fallback: find links that look like job postings by text content
                    all_links = await page.query_selector_all('a[href]')
                    job_keywords = {'engineer', 'developer', 'devops', 'sre', 'cloud',
                                    'platform', 'infrastructure', 'backend', 'frontend',
                                    'software', 'reliability'}
                    for link in all_links:
                        try:
                            text = (await link.inner_text()).strip()
                            if len(text) > 10 and any(kw in text.lower() for kw in job_keywords):
                                job_elements.append(link)
                        except Exception:
                            continue
                    if job_elements:
                        logger.info(f"[{self.name}] Found {len(job_elements)} job-like links via fallback")

                # Parse each job element (limit to 50)
                for elem in job_elements[:50]:
                    try:
                        text_content = (await elem.inner_text()).strip()
                        title = text_content.split('\n')[0][:100]
                        href = await elem.get_attribute('href') or ''

                        if not title or len(title) < 5:
                            continue

                        # Build full URL
                        if href.startswith('/'):
                            href = self.base_url + href
                        elif not href.startswith('http'):
                            continue

                        # Try to extract company from text lines
                        company = "Unknown"
                        text_parts = text_content.split('\n')
                        text_parts = [p.strip() for p in text_parts if p.strip()]
                        if len(text_parts) > 1:
                            company = text_parts[1][:50]

                        # Extract location hint
                        job_location = "Remote"
                        for part in text_parts:
                            part_lower = part.lower()
                            if any(w in part_lower for w in ['remote', 'hybrid', 'onsite', 'on-site']):
                                job_location = part.strip()[:50]
                                break

                        job = Job(
                            title=title,
                            company=company,
                            location=job_location,
                            description="",  # Would need clicking into each job
                            apply_url=href,
                            source=self.name,
                            remote=True,  # JobSurface focuses on remote roles
                        )
                        jobs.append(job)
                    except Exception as e:
                        logger.debug(f"[{self.name}] Error parsing element: {e}")
                        continue

                await page.close()

        except RuntimeError as e:
            # Playwright not installed
            logger.warning(f"[{self.name}] {e}")
        except Exception as e:
            logger.error(f"[{self.name}] Scraping failed: {e}")

        jobs = self.deduplicate(jobs)
        logger.info(f"[{self.name}] Returning {len(jobs)} jobs for '{query}'")
        return jobs
