#!/usr/bin/env python3
"""
Job Automation Pipeline — Main Orchestrator
=============================================
Runs the full daily pipeline:
  1. Scrape jobs from enabled sources
  2. Deduplicate across sources
  3. Match & score against resume profiles using Claude
  4. Tailor resumes for matched jobs
  5. Generate cover letters
  6. Compile LaTeX → PDF
  7. Update the Excel tracker

Usage:
  python main.py                    # Full run with config.yaml
  python main.py --config my.yaml   # Custom config
  python main.py --dry-run          # Scrape + match only, no generation
  python main.py --scrape-only      # Just scrape and show results
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# Fix SSL for Python 3.14+ / OpenSSL 3.6+ where certifi PEM loading is broken.
# urllib3 fails with "NO_CERTIFICATE_OR_CRL_FOUND" when calling load_verify_locations
# with certifi's CA bundle. Workaround: use system default SSL context instead.
import ssl as _ssl
_test_ctx = _ssl.create_default_context()
if _test_ctx.cert_store_stats()["x509_ca"] > 0:
    import urllib3.util.ssl_
    import urllib3.connection
    def _fixed_ssl_wrap(sock, keyfile=None, certfile=None, cert_reqs=None,
                        ca_certs=None, server_hostname=None, ssl_version=None,
                        ciphers=None, ssl_context=None, ca_cert_dir=None,
                        key_password=None, ca_cert_data=None, tls_in_tls=False):
        ctx = _ssl.create_default_context()
        if certfile:
            ctx.load_cert_chain(certfile, keyfile, key_password)
        if cert_reqs is not None:
            ctx.verify_mode = cert_reqs
            ctx.check_hostname = cert_reqs == _ssl.CERT_REQUIRED
        return ctx.wrap_socket(sock, server_hostname=server_hostname)
    urllib3.util.ssl_.ssl_wrap_socket = _fixed_ssl_wrap
    urllib3.connection.ssl_wrap_socket = _fixed_ssl_wrap

# Load .env file if present (for local development)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _val = _val.strip().strip("'\"")
                if _key.strip() and _val:
                    os.environ.setdefault(_key.strip(), _val)

def _setup_logging():
    """Configure root logger: compact console output + detailed file output."""
    Path("output").mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — compact single-letter level prefix, INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname).1s] %(message)s"))

    # File handler — full details at DEBUG level
    file_handler = logging.FileHandler("output/pipeline.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))

    root.addHandler(console)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


from scrapers import (
    SerpAPIScraper, JSearchScraper, AdzunaScraper,
    IndeedScraper, IrishJobsScraper, LinkedInScraper,
    WorkAtAStartupScraper, HackerNewsScraper,
)
from scrapers.base import Job, BaseScraper
from ai_client import AIClient
from matcher import match_jobs
from tailorer import tailor_resume
from resume_scorer import score_and_improve
from contact_finder import find_contacts_batch
from cover_letter import generate_cover_letter
from latex_compiler import compile_tex_to_pdf
from excel_tracker import create_or_update_tracker
from email_notifier import send_summary_email


def load_config(config_path: str = "config.yaml") -> dict:
    """Load and validate configuration."""
    path = Path(config_path)
    if not path.exists():
        logger.critical(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(path) as f:
        config = yaml.safe_load(f)

    # Resolve environment variables in API keys
    for key, value in config.get("api_keys", {}).items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            config["api_keys"][key] = os.environ.get(env_var, "")

    return config


def resolve_api_key(config: dict, key_name: str) -> str:
    """Get API key from config, falling back to environment variable."""
    val = config.get("api_keys", {}).get(key_name, "")
    if not val:
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "serpapi": "SERPAPI_API_KEY",
            "jsearch": "JSEARCH_API_KEY",
            "adzuna_app_id": "ADZUNA_APP_ID",
            "adzuna_app_key": "ADZUNA_APP_KEY",
        }
        val = os.environ.get(env_map.get(key_name, ""), "")
    return val


def init_scrapers(config: dict) -> List[BaseScraper]:
    """Initialize enabled scrapers with API keys."""
    scrapers = []
    enabled = config.get("scrapers", {}).get("enabled", [])
    delay = config.get("scrapers", {}).get("delay_between_requests", 2)

    if "serpapi" in enabled:
        key = resolve_api_key(config, "serpapi")
        if key:
            scrapers.append(SerpAPIScraper(api_key=key, delay=delay))
        else:
            logger.warning("SerpAPI enabled but no API key found. Skipping.")

    if "jsearch" in enabled:
        key = resolve_api_key(config, "jsearch")
        if key:
            scrapers.append(JSearchScraper(api_key=key, delay=delay))
        else:
            logger.warning("JSearch enabled but no API key found. Skipping.")

    if "adzuna" in enabled:
        app_id = resolve_api_key(config, "adzuna_app_id")
        app_key = resolve_api_key(config, "adzuna_app_key")
        if app_id and app_key:
            scrapers.append(AdzunaScraper(app_id=app_id, app_key=app_key, delay=delay))
        else:
            logger.warning("Adzuna enabled but missing credentials. Skipping.")

    # Browser-based scrapers (no API key needed — just Playwright)
    browser_cfg = config.get("scrapers", {}).get("browser", {})
    max_pages = browser_cfg.get("max_pages", 2)

    if "linkedin" in enabled:
        geo_id = browser_cfg.get("linkedin_geo_id", "104738515")
        scrapers.append(LinkedInScraper(max_pages=max_pages, geo_id=geo_id))
        logger.info("LinkedIn scraper enabled (Playwright stealth)")

    if "indeed" in enabled:
        country = browser_cfg.get("indeed_country", "ie")
        scrapers.append(IndeedScraper(country=country, max_pages=max_pages))
        logger.info("Indeed scraper enabled (Playwright stealth)")

    if "irishjobs" in enabled:
        scrapers.append(IrishJobsScraper(max_pages=max_pages))
        logger.info("IrishJobs.ie scraper enabled (Playwright stealth)")

    # Startup scrapers (lightweight, no browser needed)
    if "yc_wats" in enabled:
        scrapers.append(WorkAtAStartupScraper())
        logger.info("YC Work at a Startup scraper enabled")

    if "hn_hiring" in enabled:
        scrapers.append(HackerNewsScraper())
        logger.info("HN Who's Hiring scraper enabled")

    return scrapers


def load_resumes(config: dict) -> Dict[str, str]:
    """Load LaTeX resume source files."""
    resumes = {}
    config_dir = Path(".")  # Assumes running from job_automation/

    for key, info in config.get("resumes", {}).items():
        tex_path = config_dir / info["tex_path"]
        if tex_path.exists():
            resumes[key] = tex_path.read_text(encoding="utf-8")
            logger.info(f"Loaded resume '{key}': {tex_path}")
        else:
            logger.warning(f"Resume '{key}' not found at {tex_path}")

    return resumes


BROWSER_SCRAPERS = {"linkedin", "indeed", "irishjobs"}

# Consolidated queries for browser scrapers — broad enough to catch everything,
# few enough to not take 30 minutes. Each of these covers multiple specific titles.
BROWSER_QUERIES = [
    "DevOps OR SRE OR Platform Engineer",
    "Software Engineer OR Developer",
    "Full Stack OR Backend OR Frontend",
    "Cloud Engineer OR Infrastructure",
    "Graduate OR Junior Engineer",
]


def _scrape_single(scraper: BaseScraper, query: str, location: str, days_back: int) -> List[Job]:
    """Run one scraper query (used for parallel execution)."""
    try:
        jobs = scraper.search(query, location, days_back=days_back)
        if jobs:
            logger.info(f"[{scraper.name}] '{query}' in '{location}' -> {len(jobs)} jobs")
        return jobs
    except Exception as e:
        logger.error(f"[{scraper.name}] ERROR for '{query}': {e}")
        return []


def scrape_all_jobs(scrapers: List[BaseScraper], config: dict) -> List[Job]:
    """Run all scrapers with smart query routing.

    Strategy:
    - API scrapers (fast, no browser): run ALL queries × ALL locations
    - Browser scrapers (slow, stealth): run 5 consolidated queries × primary locations only
    - Global timeout: abort if scraping exceeds max_scrape_minutes

    This keeps total scrape time under 10 minutes instead of 45+.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    all_jobs = []
    queries = config["search"]["queries"]
    primary_locs = config["search"]["locations"]["primary"]
    secondary_locs = config["search"]["locations"]["secondary"]
    days_back = config["search"].get("days_back", 3)
    max_scrape_min = config.get("scrapers", {}).get("max_scrape_minutes", 12)
    deadline = _time.time() + max_scrape_min * 60

    # Separate scrapers into fast (API) and slow (browser)
    api_scrapers = [s for s in scrapers if s.name not in BROWSER_SCRAPERS]
    browser_scrapers = [s for s in scrapers if s.name in BROWSER_SCRAPERS]

    # Build tasks — different strategies for API vs browser
    # Rate-limited scrapers (jsearch: 200 req/mo free) get fewer tasks
    RATE_LIMITED = {"jsearch", "adzuna"}
    api_tasks = []
    browser_tasks = []

    for scraper in api_scrapers:
        if scraper.name in RATE_LIMITED:
            # Conserve quota: primary locations only, top 5 queries on secondary
            for query in queries:
                for location in primary_locs:
                    api_tasks.append((scraper, query, location))
            for query in queries[:5]:
                for location in secondary_locs:
                    api_tasks.append((scraper, query, location))
        else:
            for query in queries:
                for location in primary_locs + secondary_locs:
                    api_tasks.append((scraper, query, location))

    for scraper in browser_scrapers:
        # Browser scrapers: consolidated queries × primary locations only
        for query in BROWSER_QUERIES:
            for location in primary_locs:
                browser_tasks.append((scraper, query, location))

    total = len(api_tasks) + len(browser_tasks)
    logger.info(f"API tasks: {len(api_tasks)}, Browser tasks: {len(browser_tasks)} (total: {total})")
    logger.info(f"Deadline: {max_scrape_min} minutes")

    max_workers = config.get("scrapers", {}).get("max_workers", 8)

    # Phase 1: Run API scrapers (fast, high parallelism)
    if api_tasks:
        logger.info(f"[Phase 1] Running {len(api_tasks)} API scraper tasks...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_scrape_single, s, q, l, days_back): (s.name, q, l)
                for s, q, l in api_tasks
            }
            for future in as_completed(futures):
                if _time.time() > deadline:
                    logger.warning("API phase exceeded deadline, moving on...")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    jobs = future.result(timeout=30)
                    all_jobs.extend(jobs)
                except Exception as e:
                    name, q, l = futures[future]
                    logger.error(f"[{name}] Failed: '{q}' — {e}")

    # Phase 2: Run browser scrapers (slow, limited parallelism)
    # Only 2 workers for browser scrapers to avoid overwhelming Playwright
    if browser_tasks and _time.time() < deadline:
        remaining = int(deadline - _time.time())
        logger.info(f"[Phase 2] Running {len(browser_tasks)} browser tasks ({remaining}s remaining)...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(_scrape_single, s, q, l, days_back): (s.name, q, l)
                for s, q, l in browser_tasks
            }
            for future in as_completed(futures):
                if _time.time() > deadline:
                    logger.warning("Browser phase exceeded deadline, stopping.")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    jobs = future.result(timeout=60)
                    all_jobs.extend(jobs)
                except Exception as e:
                    name, q, l = futures[future]
                    logger.error(f"[{name}] Failed: '{q}' — {e}")
    elif browser_tasks:
        logger.info("No time left for browser scrapers, skipping.")

    return all_jobs


def _normalize_company(name: str) -> str:
    """Normalize company name for dedup (strip common suffixes)."""
    import re
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [" ltd", " limited", " inc", " inc.", " incorporated",
                   " gmbh", " ag", " llc", " plc", " corp", " corporation",
                   " co.", " co", " s.a.", " s.a", " b.v.", " group",
                   " ireland", " uk", " us", " technologies", " technology",
                   " solutions", " services", " consulting"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name).strip()
    return name


def _similarity(a: str, b: str) -> float:
    """Quick similarity ratio (0-1) between two strings."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def global_deduplicate(jobs: List[Job]) -> List[Job]:
    """Remove duplicates across all sources using fuzzy matching.

    Two jobs are considered duplicates if:
    - Company similarity > 80% AND title similarity > 85%
    This handles "Google" vs "Google Ireland Ltd", etc.
    """
    unique = []
    seen_keys = []  # List of (normalized_title, normalized_company) tuples

    for job in jobs:
        norm_title = job.title.lower().strip()
        norm_company = _normalize_company(job.company)

        is_dupe = False
        for seen_title, seen_company in seen_keys:
            company_sim = _similarity(norm_company, seen_company)
            if company_sim > 0.80:
                title_sim = _similarity(norm_title, seen_title)
                if title_sim > 0.85:
                    is_dupe = True
                    break

        if not is_dupe:
            seen_keys.append((norm_title, norm_company))
            unique.append(job)

    return unique


# ── Seen-Jobs Persistence ──────────────────────────────────────────────

def _load_seen_jobs(path: Path) -> dict:
    """Load seen_jobs.json — {job_id: {first_seen, last_seen, score}}."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_seen_jobs(seen: dict, path: Path):
    """Save seen_jobs.json."""
    with open(path, "w") as f:
        json.dump(seen, f, indent=2)


def _filter_new_jobs(jobs: List[Job], seen: dict, run_date: str) -> List[Job]:
    """Filter out already-seen jobs. Updates seen dict with new entries."""
    new_jobs = []
    for job in jobs:
        if job.job_id not in seen:
            seen[job.job_id] = {
                "first_seen": run_date,
                "last_seen": run_date,
                "title": job.title,
                "company": job.company,
                "score": 0,
                "matched": False,
            }
            new_jobs.append(job)
        else:
            seen[job.job_id]["last_seen"] = run_date
    return new_jobs


# ── Pre-Cutoff Ranking (Local, No AI) ──────────────────────────────────

def _rank_jobs_locally(jobs: List[Job], config: dict) -> List[Job]:
    """Rank jobs by keyword relevance + recency before the max_jobs cutoff.

    This ensures we send the most promising jobs to AI matching,
    not just whatever scraped first.
    """
    from datetime import datetime, timedelta

    # Build keyword set from config
    target_roles = set()
    for resume_info in config.get("resumes", {}).values():
        for role in resume_info.get("target_roles", []):
            for word in role.lower().split():
                if len(word) > 2:
                    target_roles.add(word)

    # Also add query terms
    for query in config.get("search", {}).get("queries", []):
        for word in query.lower().split():
            if len(word) > 2:
                target_roles.add(word)

    def score_job(job: Job) -> float:
        """Local relevance score (0-100). Higher = more relevant."""
        score = 0.0
        title_lower = job.title.lower()
        desc_lower = job.description.lower() if job.description else ""

        # Title keyword matches (most important)
        title_words = set(title_lower.split())
        title_matches = len(title_words & target_roles)
        score += title_matches * 15  # Up to ~60 points for 4 matches

        # Description keyword matches (less weight)
        if desc_lower:
            desc_matches = sum(1 for kw in target_roles if kw in desc_lower)
            score += min(desc_matches * 3, 20)  # Cap at 20 points

        # Location bonus (Dublin/Ireland/Remote preferred)
        loc = job.location.lower()
        if "dublin" in loc:
            score += 10
        elif "ireland" in loc:
            score += 8
        elif "remote" in loc:
            score += 6

        # Recency bonus
        if job.posted_date:
            try:
                posted = datetime.fromisoformat(job.posted_date.replace("Z", "+00:00"))
                days_old = (datetime.now(posted.tzinfo) - posted).days if posted.tzinfo else 0
                if days_old <= 1:
                    score += 10
                elif days_old <= 3:
                    score += 5
            except (ValueError, TypeError):
                pass

        # Has description bonus (jobs with descriptions score better in AI matching)
        if len(desc_lower) > 100:
            score += 5

        return score

    return sorted(jobs, key=score_job, reverse=True)


# ── Quick-Reject Pre-Filter ────────────────────────────────────────────

_REJECT_TITLE_PATTERNS = [
    "director", "vice president", "vp ", "vp,", "chief ",
    "head of", "principal architect",
]

_REJECT_DESC_PATTERNS = [
    "security clearance required", "ts/sci", "top secret",
    "10+ years", "12+ years", "15+ years", "8+ years of experience",
]


def _quick_reject(jobs: List[Job]) -> List[Job]:
    """Filter out jobs that are obviously not a match.

    Removes: Senior Director+, security clearance, 8+ years required.
    Saves 30-50% of AI matching tokens.
    """
    filtered = []
    rejected = 0

    for job in jobs:
        title_lower = job.title.lower()
        desc_lower = (job.description or "").lower()

        # Reject by title
        if any(pattern in title_lower for pattern in _REJECT_TITLE_PATTERNS):
            rejected += 1
            continue

        # Reject by description keywords
        if any(pattern in desc_lower for pattern in _REJECT_DESC_PATTERNS):
            rejected += 1
            continue

        filtered.append(job)

    if rejected:
        logger.info(f"Quick-reject filtered {rejected} obviously unsuitable jobs")

    return filtered


def run_pipeline(config: dict, dry_run: bool = False, scrape_only: bool = False):
    """Execute the full pipeline."""
    run_date = datetime.now().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  JOB AUTOMATION PIPELINE — {run_date} {run_time}")
    print(f"{'='*60}\n")

    # Create daily output directory
    base_dir = Path(config["output"]["base_dir"])
    daily_dir = base_dir / run_date
    daily_dir.mkdir(parents=True, exist_ok=True)
    resumes_dir = daily_dir / "resumes"
    resumes_dir.mkdir(exist_ok=True)
    coverletters_dir = daily_dir / "cover_letters"
    coverletters_dir.mkdir(exist_ok=True)

    # --- Step 1: Initialize scrapers ---
    logger.info("Initializing scrapers...")
    scrapers = init_scrapers(config)
    if not scrapers:
        logger.critical("No scrapers initialized. Check API keys in config.yaml")
        sys.exit(1)
    logger.info(f"Active scrapers: {[s.name for s in scrapers]}")

    # --- Step 2: Scrape jobs ---
    logger.info("Scraping jobs...")
    raw_jobs = scrape_all_jobs(scrapers, config)
    logger.info(f"Total raw results: {len(raw_jobs)}")

    # --- Step 3: Deduplicate (fuzzy matching) ---
    logger.info("Deduplicating (fuzzy matching)...")
    unique_jobs = global_deduplicate(raw_jobs)
    logger.info(f"Unique jobs: {len(unique_jobs)} (removed {len(raw_jobs) - len(unique_jobs)} dupes)")

    # --- Step 3b: Filter already-seen jobs ---
    seen_path = base_dir / "seen_jobs.json"
    seen_jobs = _load_seen_jobs(seen_path)
    new_jobs = _filter_new_jobs(unique_jobs, seen_jobs, run_date)
    _save_seen_jobs(seen_jobs, seen_path)
    logger.info(f"New jobs: {len(new_jobs)} (already seen: {len(unique_jobs) - len(new_jobs)})")

    if scrape_only:
        logger.info(f"[SCRAPE-ONLY MODE] Dumping {len(new_jobs)} new jobs:")
        for j in new_jobs:
            logger.info(f"  - {j.title} @ {j.company} ({j.location}) [{j.source}]")
            if j.apply_url:
                logger.info(f"    Apply: {j.apply_url}")
        # Save raw results as JSON
        raw_path = daily_dir / "raw_jobs.json"
        with open(raw_path, "w") as f:
            json.dump([j.to_dict() for j in new_jobs], f, indent=2)
        logger.info(f"Saved to: {raw_path}")
        return

    # --- Step 3c: Quick-reject obvious non-matches ---
    filtered_jobs = _quick_reject(new_jobs)

    # --- Step 4: Load resumes and match ---
    logger.info("Loading resumes and initializing AI client...")
    resumes = load_resumes(config)
    if not resumes:
        logger.critical("No resumes loaded. Check paths in config.yaml")
        sys.exit(1)

    try:
        ai_client = AIClient.from_config(config)
    except Exception as e:
        logger.critical(str(e))
        sys.exit(1)

    max_jobs = config["search"].get("max_jobs_per_run", 20)
    min_score = config["search"].get("min_match_score", 60)

    # Rank by local relevance BEFORE cutoff (best jobs first)
    ranked_jobs = _rank_jobs_locally(filtered_jobs, config)
    jobs_to_match = ranked_jobs[:max_jobs]
    logger.info(f"Matching {len(jobs_to_match)} jobs (max_per_run: {max_jobs}, from {len(filtered_jobs)} candidates)...")

    matched_jobs = match_jobs(
        jobs=jobs_to_match,
        resumes=resumes,
        ai_client=ai_client,
        min_score=min_score,
    )
    logger.info(f"Matched jobs (avg >= {min_score}): {len(matched_jobs)}")

    # Update seen_jobs with match scores
    for job in matched_jobs:
        if job.job_id in seen_jobs:
            seen_jobs[job.job_id]["score"] = job.match_score
            seen_jobs[job.job_id]["matched"] = True
    _save_seen_jobs(seen_jobs, seen_path)

    if dry_run:
        logger.info(f"[DRY-RUN MODE] Would process {len(matched_jobs)} jobs:")
        for j in matched_jobs:
            logger.info(f"  ATS={j.ats_score} HM={j.hiring_manager_score} TR={j.tech_recruiter_score} (avg={j.match_score}) | {j.title} @ {j.company} -> {j.matched_resume}")
        return

    if not matched_jobs:
        logger.info("No jobs matched above the threshold. Nothing to generate.")
        # Still update tracker with scraped-but-unmatched stats
        tracker_path = base_dir / config["output"]["tracker_filename"]
        create_or_update_tracker([], str(tracker_path), run_date)
        return

    # --- Step 5: Tailor resumes + 3-score validation ---
    # Initial ATS/HM/TR scores from matching are against the BASE resume.
    # After tailoring, we re-score the TAILORED resume and iteratively
    # improve until all 3 scores hit 85+ (up to 3 rounds).
    logger.info("Tailoring resumes + scoring to 85+ (ATS, HM, TR)...")
    for job in matched_jobs:
        base_tex = resumes.get(job.matched_resume, "")
        if not base_tex:
            logger.info(f"Skipping {job.title} @ {job.company}: no base resume for profile '{job.matched_resume}'")
            continue

        logger.info(f"--- {job.title} @ {job.company} ---")
        logger.info(f"Base scores: ATS={job.ats_score} HM={job.hiring_manager_score} TR={job.tech_recruiter_score}")

        # First pass: tailor the resume using match data
        tailor_resume(
            job=job,
            base_tex=base_tex,
            ai_client=ai_client,
            output_dir=resumes_dir,
        )

        # Second pass: re-score the tailored version and improve until 85+
        if job.tailored_tex_path and Path(job.tailored_tex_path).exists():
            tailored_tex = Path(job.tailored_tex_path).read_text(encoding="utf-8")

            improved_tex, scores = score_and_improve(
                tailored_tex=tailored_tex,
                job=job,
                ai_client=ai_client,
                min_score=85,
                max_rounds=3,
            )

            # Update with post-tailoring scores (these go into the tracker)
            job.ats_score = scores.get("ats_score", 0)
            job.hiring_manager_score = scores.get("hiring_manager_score", 0)
            job.tech_recruiter_score = scores.get("tech_recruiter_score", 0)
            job.match_score = round((job.ats_score + job.hiring_manager_score + job.tech_recruiter_score) / 3, 1)

            # Save the improved version
            if improved_tex != tailored_tex:
                Path(job.tailored_tex_path).write_text(improved_tex, encoding="utf-8")

    # --- Step 6: Find LinkedIn contacts ---
    logger.info("Finding LinkedIn contacts for networking...")
    find_contacts_batch(matched_jobs, ai_client)

    # --- Step 7: Generate cover letters ---
    logger.info("Generating cover letters...")
    for job in matched_jobs:
        # Use the tailored (and scored/improved) resume for context
        if job.tailored_tex_path and Path(job.tailored_tex_path).exists():
            resume_for_cl = Path(job.tailored_tex_path).read_text(encoding="utf-8")
        else:
            resume_for_cl = resumes.get(job.matched_resume, "")

        generate_cover_letter(
            job=job,
            resume_tex=resume_for_cl,
            ai_client=ai_client,
            output_dir=coverletters_dir,
        )

    # --- Step 8: Compile LaTeX → PDF ---
    logger.info("Compiling LaTeX to PDF...")
    for job in matched_jobs:
        if job.tailored_tex_path:
            pdf = compile_tex_to_pdf(job.tailored_tex_path)
            job.tailored_pdf_path = pdf
        if job.cover_letter_tex_path:
            pdf = compile_tex_to_pdf(job.cover_letter_tex_path)
            job.cover_letter_pdf_path = pdf

    # --- Step 9: Update master Excel tracker ---
    logger.info("Updating master Excel tracker...")
    tracker_path = base_dir / config["output"]["tracker_filename"]
    create_or_update_tracker(matched_jobs, str(tracker_path), run_date)

    # --- Summary ---
    resumes_generated = sum(1 for j in matched_jobs if j.tailored_pdf_path)
    cls_generated = sum(1 for j in matched_jobs if j.cover_letter_pdf_path)
    all_85_count = sum(1 for j in matched_jobs if j.ats_score >= 85 and j.hiring_manager_score >= 85 and j.tech_recruiter_score >= 85)

    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE — {run_date}")
    print(f"{'='*60}")
    print(f"  Jobs scraped:        {len(raw_jobs)}")
    print(f"  Unique jobs:         {len(unique_jobs)}")
    print(f"  Jobs matched:        {len(matched_jobs)}")
    print(f"  All 3 scores 85+:   {all_85_count}/{len(matched_jobs)}")
    print(f"  Resumes generated:   {resumes_generated}")
    print(f"  Cover letters:       {cls_generated}")
    print(f"  Tracker:             {tracker_path}")
    print(f"  Output directory:    {daily_dir}")
    ai_stats = ai_client.stats
    print(f"  AI cache hits:       {ai_stats['cache_hits']}")
    print(f"  AI cache misses:     {ai_stats['cache_misses']}")
    print(f"  AI provider calls:   {ai_stats['provider_calls']}")
    print(f"{'='*60}\n")

    # Save run metadata
    meta = {
        "run_date": run_date,
        "run_time": run_time,
        "raw_jobs": len(raw_jobs),
        "unique_jobs": len(unique_jobs),
        "matched_jobs": len(matched_jobs),
        "resumes_generated": resumes_generated,
        "cover_letters_generated": cls_generated,
        "matched_details": [
            {
                "title": j.title,
                "company": j.company,
                "score": j.match_score,
                "resume_type": j.matched_resume,
                "apply_url": j.apply_url,
            }
            for j in matched_jobs
        ],
    }
    meta_path = daily_dir / "run_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # --- Step 10: Email notification ---
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
    notify_email = os.environ.get("NOTIFY_EMAIL", gmail_addr)
    if gmail_addr and gmail_pass:
        logger.info("Sending email summary...")
        send_summary_email(
            matched_jobs=matched_jobs,
            raw_count=len(raw_jobs),
            unique_count=len(unique_jobs),
            gmail_address=gmail_addr,
            gmail_app_password=gmail_pass,
            recipient=notify_email,
        )
    else:
        logger.info("Email skipped (set GMAIL_ADDRESS + GMAIL_APP_PASSWORD to enable)")


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(description="Job Automation Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + match only, no generation")
    parser.add_argument("--scrape-only", action="store_true", help="Just scrape and show results")
    args = parser.parse_args()

    config = load_config(args.config)
    run_pipeline(config, dry_run=args.dry_run, scrape_only=args.scrape_only)


if __name__ == "__main__":
    main()
