"""JobSurface scraper -- remote DevOps & Cloud jobs.

jobsurface.com sources hidden jobs from company career pages.
Uses Playwright with stealth mode due to Cloudflare Turnstile protection.
Site is a React SPA (Remix) -- requires JavaScript rendering.
"""

from __future__ import annotations
import logging
import requests as http_requests
from typing import List
from .base import Job, BaseScraper
from .browser import stealth_browser, run_async

logger = logging.getLogger(__name__)


class JobSurfaceScraper(BaseScraper):
    """Scraper for jobsurface.com remote DevOps/Cloud jobs."""

    name = "jobsurface"

    _site_reachable: bool | None = None
    _health_checked: bool = False

    def __init__(self, max_pages: int = 1):
        self.max_pages = max_pages
        self.base_url = "https://www.jobsurface.com"

    def _health_check(self) -> bool:
        """Quick HEAD request to verify the site is reachable. Run once per process.

        Note: JobSurface is behind Cloudflare Turnstile so a HEAD request will
        return 403 (challenge). That is fine -- it means the site is up. We only
        bail if the connection itself fails (DNS / TCP timeout).
        """
        if JobSurfaceScraper._health_checked:
            return JobSurfaceScraper._site_reachable is not False
        JobSurfaceScraper._health_checked = True
        try:
            resp = http_requests.head(self.base_url, timeout=5, allow_redirects=True)
            # 403 from Cloudflare challenge is expected -- site is up
            if resp.status_code in (200, 403):
                JobSurfaceScraper._site_reachable = True
                return True
        except http_requests.RequestException:
            pass
        logger.warning("[JobSurface] Site unreachable (health check failed) -- skipping all queries this run")
        JobSurfaceScraper._site_reachable = False
        return False

    def search(self, query: str, location: str = "", days_back: int = 7, **kwargs) -> List[Job]:
        """Search jobsurface.com for jobs matching the query."""
        if JobSurfaceScraper._site_reachable is False:
            return []
        if not self._health_check():
            return []
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

                # Wait for Cloudflare challenge to resolve + React hydration
                await browser.human_delay(4000, 7000)

                # Check if Cloudflare challenge is still showing
                content = await page.content()
                if "Just a moment" in content or "challenge-platform" in content:
                    logger.info(f"[{self.name}] Cloudflare challenge detected, waiting longer...")
                    try:
                        await page.wait_for_function(
                            "() => !document.body.innerText.includes('Just a moment')",
                            timeout=15000,
                        )
                        await browser.human_delay(2000, 4000)
                    except Exception:
                        logger.warning(f"[{self.name}] Cloudflare challenge didn't resolve")
                        return []

                # Scroll to trigger lazy loading
                await browser.scroll_page(page, scrolls=3)
                await browser.human_delay(2000, 3000)

                # Try to find and use a search input if available
                try:
                    search_input = await page.query_selector(
                        'input[type="search"], input[type="text"], '
                        'input[placeholder*="search" i], input[placeholder*="Search" i], '
                        'input[name="q"], input[name="search"], input[name="query"]'
                    )
                    if search_input:
                        await search_input.fill(query)
                        await search_input.press("Enter")
                        await browser.human_delay(3000, 5000)
                except Exception:
                    pass  # No search input found -- browse what's shown

                # Extract job cards using multiple selector strategies
                # (Remix/React SPAs often use these patterns)
                selectors = [
                    'a[href*="/job/"]',
                    'a[href*="/jobs/"]',
                    'a[href*="/position/"]',
                    'a[href*="/posting/"]',
                    '[class*="job-card"]',
                    '[class*="JobCard"]',
                    '[class*="job-list"] a',
                    '[class*="jobs-list"] a',
                    '[data-testid*="job"]',
                    'article a',
                    '.job-listing',
                    'main a[href]',  # Fallback: links in main content area
                ]

                job_elements = []
                for selector in selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        if elements and len(elements) >= 3:  # Need at least 3 to look like a listing
                            job_elements = elements
                            logger.info(f"[{self.name}] Found {len(elements)} elements with selector '{selector}'")
                            break
                    except Exception:
                        continue

                if not job_elements:
                    # Fallback: find links that look like job postings by text content
                    all_links = await page.query_selector_all('a[href]')
                    job_keywords = {
                        'engineer', 'developer', 'devops', 'sre', 'cloud',
                        'platform', 'infrastructure', 'backend', 'frontend',
                        'software', 'reliability', 'architect', 'manager',
                        'analyst', 'data', 'security', 'network', 'senior',
                        'junior', 'lead', 'staff', 'principal',
                    }
                    for link in all_links:
                        try:
                            text = (await link.inner_text()).strip()
                            href = await link.get_attribute('href') or ''
                            # Filter: must have job-like text AND a real href
                            if (len(text) > 10 and len(text) < 200
                                    and any(kw in text.lower() for kw in job_keywords)
                                    and href and href != '#'
                                    and not any(skip in href.lower() for skip in [
                                        'login', 'signup', 'register', 'about', 'faq',
                                        'contact', 'blog', 'privacy', 'terms'])):
                                job_elements.append(link)
                        except Exception:
                            continue
                    if job_elements:
                        logger.info(f"[{self.name}] Found {len(job_elements)} job-like links via fallback")
                    else:
                        # Log a sample of the page for debugging
                        try:
                            body_text = await page.inner_text('body')
                            logger.debug(f"[{self.name}] Page text sample (first 500 chars): {body_text[:500]}")
                        except Exception:
                            pass

                # Parse each job element (limit to 50)
                seen_titles = set()
                for elem in job_elements[:50]:
                    try:
                        text_content = (await elem.inner_text()).strip()
                        if not text_content:
                            continue
                        title = text_content.split('\n')[0].strip()[:100]
                        href = await elem.get_attribute('href') or ''

                        if not title or len(title) < 5:
                            continue

                        # Deduplicate by title within this page
                        title_key = title.lower().strip()
                        if title_key in seen_titles:
                            continue
                        seen_titles.add(title_key)

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
