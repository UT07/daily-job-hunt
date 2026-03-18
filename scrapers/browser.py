"""Shared Playwright browser engine with anti-detection stealth.

Handles Cloudflare, bot detection, and rate limiting across all
browser-based scrapers.
"""

from __future__ import annotations
import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports — fail gracefully if playwright not installed
_pw = None
_stealth_obj = None
_stealth_checked = False


def _ensure_imports():
    global _pw, _stealth_obj, _stealth_checked
    if _pw is None:
        try:
            from playwright.async_api import async_playwright
            _pw = async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run:\n"
                "  pip install playwright playwright-stealth\n"
                "  playwright install chromium"
            )
    if not _stealth_checked:
        _stealth_checked = True
        try:
            from playwright_stealth import Stealth
            _stealth_obj = Stealth()
        except ImportError:
            _stealth_obj = None
            logger.warning("[BROWSER] playwright-stealth not installed. Anti-detection will be limited.")


# Realistic browser fingerprints
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 2560, "height": 1440},
]


class StealthBrowser:
    """Manages a stealth Playwright browser session with anti-detection."""

    def __init__(self, headless: bool = True, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._playwright = None
        self._browser = None

    async def start(self):
        _ensure_imports()
        self._playwright = await _pw().start()
        ua = random.choice(USER_AGENTS)
        vp = random.choice(VIEWPORTS)

        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-http2",  # fixes ERR_HTTP2_PROTOCOL_ERROR with Akamai/Cloudflare CDNs
                f"--window-size={vp['width']},{vp['height']}",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="en-IE",
            timezone_id="Europe/Dublin",
            geolocation={"latitude": 53.3498, "longitude": -6.2603},  # Dublin
            permissions=["geolocation"],
            java_script_enabled=True,
        )

        # Apply stealth patches if available (playwright-stealth v2 API)
        if _stealth_obj:
            for page in self._context.pages:
                await _stealth_obj.apply_stealth_async(page)

        return self

    async def new_page(self):
        """Create a new stealth page."""
        page = await self._context.new_page()
        if _stealth_obj:
            await _stealth_obj.apply_stealth_async(page)

        # Override navigator.webdriver detection
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-IE', 'en-GB', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)
        return page

    async def safe_goto(self, page, url: str, wait_until: str = "domcontentloaded",
                        timeout: int = 30000, retries: int = 2) -> bool:
        """Navigate with retry logic and Cloudflare wait."""
        for attempt in range(retries + 1):
            try:
                resp = await page.goto(url, wait_until=wait_until, timeout=timeout)

                # Check for Cloudflare challenge page
                content = await page.content()
                if "challenge-platform" in content or "Just a moment" in content:
                    logger.info("[BROWSER] Cloudflare challenge detected, waiting...")
                    await page.wait_for_timeout(random.randint(5000, 10000))
                    # Wait for challenge to resolve
                    try:
                        await page.wait_for_function(
                            "() => !document.body.innerText.includes('Just a moment')",
                            timeout=15000,
                        )
                    except Exception:
                        if attempt < retries:
                            logger.warning(f"[BROWSER] Challenge didn't resolve, retry {attempt + 1}...")
                            await page.wait_for_timeout(random.randint(3000, 6000))
                            continue
                        return False

                if resp and resp.status == 403:
                    if attempt < retries:
                        await page.wait_for_timeout(random.randint(5000, 12000))
                        continue
                    return False

                return True

            except Exception as e:
                if attempt < retries:
                    logger.warning(f"[BROWSER] Navigation error (attempt {attempt + 1}): {e}")
                    await page.wait_for_timeout(random.randint(3000, 8000))
                else:
                    logger.error(f"[BROWSER] Failed to load {url}: {e}")
                    return False

        return False

    async def human_delay(self, min_ms: int = 800, max_ms: int = 3000):
        """Random delay to simulate human browsing patterns."""
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)

    async def scroll_page(self, page, scrolls: int = 3):
        """Simulate human scrolling behavior."""
        for _ in range(scrolls):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
            await self.human_delay(500, 1500)

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


@asynccontextmanager
async def stealth_browser(headless: bool = True):
    """Context manager for a stealth browser session."""
    browser = StealthBrowser(headless=headless)
    await browser.start()
    try:
        yield browser
    finally:
        await browser.close()


def run_async(coro):
    """Helper to run async scraper functions from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)
