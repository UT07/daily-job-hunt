"""Entrypoint for Fargate scraper tasks.

Reads SCRAPER_SOURCE env var and dispatches to the appropriate scraper.
Used as: python3 -m scrapers.playwright.main
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scraper")

SCRAPERS = {
    "linkedin": ("scrapers.playwright.linkedin", "LinkedInScraper"),
    "indeed": ("scrapers.playwright.indeed", "IndeedScraper"),
    "glassdoor": ("scrapers.playwright.glassdoor", "GlassdoorScraper"),
    "irish_portals": ("scrapers.playwright.irish_portals", "IrishPortalsScraper"),
    "contacts": ("scrapers.playwright.contacts", "Scraper"),
}


def main():
    source = os.environ.get("SCRAPER_SOURCE", "").lower().strip()
    if not source:
        logger.error("SCRAPER_SOURCE not set")
        sys.exit(1)

    if source not in SCRAPERS:
        logger.error(f"Unknown SCRAPER_SOURCE: {source}. Available: {list(SCRAPERS.keys())}")
        sys.exit(1)

    logger.info(f"Starting scraper: {source}")

    module_path, class_name = SCRAPERS[source]
    module = __import__(module_path, fromlist=[class_name])
    scraper_class = getattr(module, class_name)
    scraper = scraper_class()
    scraper.run()

    logger.info(f"Scraper {source} complete: {scraper.jobs_found} found, {len(scraper.new_job_hashes)} new")


if __name__ == "__main__":
    main()
