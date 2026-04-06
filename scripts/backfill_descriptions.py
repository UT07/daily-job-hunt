#!/usr/bin/env python3
"""Backfill empty job descriptions by re-fetching detail pages.

Fetches all jobs with empty/null descriptions from Supabase,
re-scrapes their detail pages using source-appropriate strategies,
and updates the DB.

Usage:
    python scripts/backfill_descriptions.py                  # All sources
    python scripts/backfill_descriptions.py --source linkedin # Just LinkedIn
    python scripts/backfill_descriptions.py --dry-run         # Preview only
    python scripts/backfill_descriptions.py --max 10          # Limit count
"""
import argparse
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            v = v.strip().strip("\"'")
            if k.strip() and v:
                os.environ.setdefault(k.strip(), v)

import httpx

logging.basicConfig(level=logging.INFO, format="[%(levelname).1s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en-GB;q=0.9,en;q=0.8",
}


def _clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _fetch_linkedin(url, proxy_url):
    """Fetch LinkedIn job description via guest API."""
    # Extract job ID from URL
    match = re.search(r"/jobs/view/(\d+)", url) or re.search(r"view/([^/?]+)", url)
    if not match:
        return None
    job_id = match.group(1)

    # Try guest API first (lightweight)
    api_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        resp = httpx.get(api_url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
        if resp.status_code == 200:
            for pattern in [
                r'<div class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>\s*</div>',
                r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
                r'<div class="[^"]*description__text[^"]*"[^>]*>(.*?)$',
            ]:
                m = re.search(pattern, resp.text, re.DOTALL)
                if m and len(m.group(1)) > 100:
                    return _clean(m.group(1))
    except Exception as e:
        logger.debug(f"LinkedIn guest API failed for {job_id}: {e}")

    # Fallback to web view
    try:
        resp = httpx.get(url, proxy=proxy_url, timeout=30, follow_redirects=True, verify=False)
        if resp.status_code == 200:
            for pattern in [
                r'<div class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>\s*</div>',
                r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
            ]:
                m = re.search(pattern, resp.text, re.DOTALL)
                if m and len(m.group(1)) > 100:
                    return _clean(m.group(1))
            # Try JSON-LD
            ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            for block in ld_blocks:
                try:
                    data = json.loads(block)
                    if isinstance(data, dict) and data.get("@type") == "JobPosting":
                        desc = _clean(data.get("description", ""))
                        if len(desc) > 100:
                            return desc
                except (ValueError, KeyError):
                    continue
    except Exception as e:
        logger.debug(f"LinkedIn web view failed for {job_id}: {e}")
    return None


def _fetch_stepstone(url, proxy_url):
    """Fetch Jobs.ie / IrishJobs detail page (StepStone platform)."""
    for use_proxy in [False, True]:
        try:
            kwargs = {"headers": HEADERS, "timeout": 20, "follow_redirects": True}
            if use_proxy and proxy_url:
                kwargs["proxy"] = proxy_url
                kwargs["verify"] = False
            resp = httpx.get(url, **kwargs)
            if resp.status_code != 200:
                continue

            # Strategy 1: StepStone data-testid
            clean = re.sub(r"<style[^>]*>.*?</style>", "", resp.text, flags=re.DOTALL)
            for pattern in [
                r'data-testid="vacancy-description"[^>]*>(.*?)</div>\s*</div>',
                r'data-testid="job-description"[^>]*>(.*?)</div>\s*</div>',
            ]:
                m = re.search(pattern, clean, re.DOTALL)
                if m and len(m.group(1)) > 100:
                    return _clean(m.group(1))

            # Strategy 2: JSON-LD
            ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            for block in ld_blocks:
                try:
                    data = json.loads(block)
                    if isinstance(data, dict) and data.get("@type") == "JobPosting":
                        desc = _clean(data.get("description", ""))
                        if len(desc) > 100:
                            return desc
                except (ValueError, KeyError):
                    continue
        except Exception as e:
            logger.debug(f"StepStone fetch failed for {url}: {e}")
    return None


def _fetch_indeed(url, proxy_url):
    """Fetch Indeed job description."""
    try:
        kwargs = {"headers": HEADERS, "timeout": 20, "follow_redirects": True}
        if proxy_url:
            kwargs["proxy"] = proxy_url
            kwargs["verify"] = False
        resp = httpx.get(url, **kwargs)
        if resp.status_code != 200:
            return None

        # Indeed uses JSON-LD or jobDescriptionText div
        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
        for block in ld_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    desc = _clean(data.get("description", ""))
                    if len(desc) > 100:
                        return desc
            except (ValueError, KeyError):
                continue

        m = re.search(r'id="jobDescriptionText"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
        if m and len(m.group(1)) > 100:
            return _clean(m.group(1))
    except Exception as e:
        logger.debug(f"Indeed fetch failed for {url}: {e}")
    return None


def _fetch_generic(url, proxy_url):
    """Generic JSON-LD fetch for any source."""
    try:
        kwargs = {"headers": HEADERS, "timeout": 20, "follow_redirects": True}
        if proxy_url:
            kwargs["proxy"] = proxy_url
            kwargs["verify"] = False
        resp = httpx.get(url, **kwargs)
        if resp.status_code != 200:
            return None

        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
        for block in ld_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    desc = _clean(data.get("description", ""))
                    if len(desc) > 100:
                        return desc
            except (ValueError, KeyError):
                continue
    except Exception as e:
        logger.debug(f"Generic fetch failed for {url}: {e}")
    return None


# Map source to fetch function
FETCHERS = {
    "linkedin": _fetch_linkedin,
    "jobs_ie": _fetch_stepstone,
    "irishjobs": _fetch_stepstone,
    "indeed": _fetch_indeed,
    "adzuna": _fetch_generic,
    "gradireland": _fetch_generic,
}


def main():
    parser = argparse.ArgumentParser(description="Backfill empty job descriptions")
    parser.add_argument("--source", help="Filter by source (linkedin, jobs_ie, etc.)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating DB")
    parser.add_argument("--max", type=int, default=0, help="Max jobs to process (0=all)")
    parser.add_argument("--proxy", help="Override proxy URL")
    args = parser.parse_args()

    from supabase import create_client
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # Get proxy URL
    proxy_url = args.proxy or os.environ.get("PROXY_URL") or os.environ.get("BRIGHTDATA_PROXY_URL")
    if not proxy_url:
        logger.warning("No proxy URL set — LinkedIn/IrishJobs fetches may fail. Set PROXY_URL in .env")

    # Fetch jobs with empty descriptions
    query = db.table("jobs").select("job_id, title, company, source, apply_url, description")
    if args.source:
        query = query.eq("source", args.source)
    result = query.execute()

    empty_jobs = [
        j for j in (result.data or [])
        if not j.get("description") or len(j.get("description") or "") < 50
    ]

    if args.max > 0:
        empty_jobs = empty_jobs[:args.max]

    logger.info(f"Found {len(empty_jobs)} jobs with empty descriptions")

    # Also update jobs_raw for future pipeline runs
    stats = {"success": 0, "failed": 0, "skipped": 0}

    for i, job in enumerate(empty_jobs, 1):
        source = job.get("source", "")
        url = job.get("apply_url", "")
        fetcher = FETCHERS.get(source, _fetch_generic)

        # Skip HN jobs (no detail page to fetch)
        if source in ("hn_hiring", "wats", "manual"):
            stats["skipped"] += 1
            continue

        # Skip non-HTTP URLs
        if not url or not url.startswith("http"):
            stats["skipped"] += 1
            continue

        logger.info(f"[{i}/{len(empty_jobs)}] {source}: {job['company']} — {job['title']}")

        desc = fetcher(url, proxy_url)
        if desc and len(desc) > 50:
            if args.dry_run:
                logger.info(f"  -> Would update: {len(desc)} chars")
            else:
                db.table("jobs").update({"description": desc[:10000]}).eq("job_id", job["job_id"]).execute()
                logger.info(f"  -> Updated: {len(desc)} chars")
            stats["success"] += 1
        else:
            logger.info(f"  -> No description found")
            stats["failed"] += 1

        # Rate limit: 1-2 seconds between requests
        time.sleep(1.5)

    logger.info(f"\nResults: {stats['success']} updated, {stats['failed']} failed, {stats['skipped']} skipped")


if __name__ == "__main__":
    main()
