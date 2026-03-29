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
  python main.py --user-id UUID     # Run for a specific Supabase user
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
    AdzunaScraper, IrishJobsScraper, LinkedInScraper,
    WorkAtAStartupScraper, HackerNewsScraper,
    JobsIeScraper, GradIrelandScraper,
    JobSurfaceScraper,
)
from scrapers.base import Job, BaseScraper
from ai_client import AIClient
from matcher import match_jobs
from tailorer import tailor_resume, tailor_resume_text, extract_base_sections
from resume_scorer import score_and_improve
from contact_finder import find_contacts_batch
from cover_letter import generate_cover_letter
import google_docs_client
from latex_compiler import compile_tex_to_pdf
from excel_tracker import create_or_update_tracker
from s3_uploader import upload_artifacts, upload_tracker
from drive_uploader import upload_artifacts as drive_upload_artifacts, upload_tracker as drive_upload_tracker
from email_notifier import send_summary_email
from pipeline_context import PipelineContext


def _job_to_supabase_row(job: Job) -> dict:
    """Convert a Job dataclass to a dict matching the Supabase 'jobs' table schema.

    Only includes columns that exist in the DB schema.  Skips local-only
    fields like tex paths and S3/Drive URLs that aren't in the DB.
    """
    return {
        "job_id": job.job_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": (job.description or "")[:10000],  # cap for DB storage
        "apply_url": job.apply_url or "",
        "source": job.source or "",
        "match_score": job.match_score,
        "ats_score": job.ats_score,
        "hiring_manager_score": job.hiring_manager_score,
        "tech_recruiter_score": job.tech_recruiter_score,
        "matched_resume": job.matched_resume or "",
        "application_status": job.application_status or "New",
        "linkedin_contacts": job.linkedin_contacts or "",
    }


def _try_init_supabase():
    """Try to initialize a SupabaseClient from environment variables.

    Returns None (with a log message) if env vars are missing or the
    supabase package is unavailable.  This keeps the pipeline working
    without Supabase configured.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        logger.info("[DB] Supabase env vars not set — running in local-only mode")
        return None
    try:
        from db_client import SupabaseClient
        return SupabaseClient(url, key)
    except Exception as e:
        logger.warning(f"[DB] Failed to initialize Supabase client: {e}")
        return None


def _resolve_user_id(config: dict) -> str:
    """Get the user_id for Supabase operations.

    Priority: DEFAULT_USER_ID env var > config.yaml default_user_id >
    deterministic UUID from profile email.
    """
    # 1. Environment variable
    uid = os.environ.get("DEFAULT_USER_ID", "")
    if uid:
        return uid

    # 2. config.yaml setting
    uid = config.get("default_user_id", "")
    if uid:
        return uid

    # 3. Derive from profile email (matches UserProfile.from_config behaviour)
    import hashlib as _hl
    email = config.get("profile", {}).get("email", "pipeline@localhost")
    return _hl.md5(email.encode()).hexdigest()


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
            "adzuna_app_id": "ADZUNA_APP_ID",
            "adzuna_app_key": "ADZUNA_APP_KEY",
        }
        val = os.environ.get(env_map.get(key_name, ""), "")
    return val


def init_scrapers(config: dict) -> List[BaseScraper]:
    """Initialize enabled scrapers with API keys.

    Multi-geo support: LinkedIn scrapers are created per geo region
    (Ireland, India, US) based on config.search.geo_regions.
    """
    scrapers = []
    enabled = config.get("scrapers", {}).get("enabled", [])
    delay = config.get("scrapers", {}).get("delay_between_requests", 2)

    # --- API scrapers ---
    if "adzuna" in enabled:
        app_id = resolve_api_key(config, "adzuna_app_id")
        app_key = resolve_api_key(config, "adzuna_app_key")
        if app_id and app_key:
            scrapers.append(AdzunaScraper(app_id=app_id, app_key=app_key, delay=delay))
        else:
            logger.warning("Adzuna enabled but missing credentials. Skipping.")

    # --- Irish job boards (lightweight HTML, no browser) ---
    if "irishjobs" in enabled:
        scrapers.append(IrishJobsScraper(max_pages=1))
        logger.info("IrishJobs.ie scraper enabled")

    if "jobs_ie" in enabled:
        scrapers.append(JobsIeScraper())
        logger.info("Jobs.ie scraper enabled")

    if "gradireland" in enabled:
        scrapers.append(GradIrelandScraper())
        logger.info("GradIreland scraper enabled")

    # --- LinkedIn (multi-geo) ---
    browser_cfg = config.get("scrapers", {}).get("browser", {})
    max_pages = browser_cfg.get("max_pages", 1)

    if "linkedin" in enabled:
        geo_regions = config.get("search", {}).get("geo_regions", [])
        if not geo_regions:
            # Backward compat: single LinkedIn scraper with default geo
            geo_id = browser_cfg.get("linkedin_geo_id", "104738515")
            scrapers.append(LinkedInScraper(max_pages=max_pages, geo_id=geo_id))
            logger.info(f"LinkedIn scraper enabled (geoId={geo_id})")
        else:
            for region in geo_regions:
                geo_id = region.get("geo_id", "104738515")
                name_tag = region.get("name", "unknown")
                scrapers.append(LinkedInScraper(max_pages=max_pages, geo_id=geo_id))
                logger.info(f"LinkedIn scraper enabled for {name_tag} (geoId={geo_id})")

    # --- Startup scrapers (lightweight, no browser) ---
    if "yc_wats" in enabled:
        scrapers.append(WorkAtAStartupScraper())
        logger.info("YC Work at a Startup scraper enabled")

    if "hn_hiring" in enabled:
        scrapers.append(HackerNewsScraper())
        logger.info("HN Who's Hiring scraper enabled")

    # --- Remote DevOps/Cloud (browser-based) ---
    if "jobsurface" in enabled:
        scrapers.append(JobSurfaceScraper())
        logger.info("JobSurface scraper enabled")

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


BROWSER_SCRAPERS = {"linkedin", "irishjobs", "jobsurface"}

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
    import time
    start = time.time()
    try:
        jobs = scraper.search(query, location, days_back=days_back)
        elapsed = time.time() - start
        # Attach timing to jobs for later per-scraper latency aggregation
        for j in jobs:
            j._scrape_time = elapsed
        if jobs:
            logger.info(f"[{scraper.name}] '{query}' in '{location}' -> {len(jobs)} jobs ({elapsed:.1f}s)")
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
    RATE_LIMITED = {"adzuna"}
    api_tasks = []
    browser_tasks = []

    for scraper in api_scrapers:
        if scraper.name in RATE_LIMITED:
            # Conserve quota: primary locations only, top 5 queries
            for query in queries[:5]:
                for location in primary_locs:
                    api_tasks.append((scraper, query, location))
        else:
            # Lightweight scrapers (jobs_ie, gradireland, yc_wats, hn_hiring)
            for query in queries:
                for location in primary_locs:
                    api_tasks.append((scraper, query, location))

    for scraper in browser_scrapers:
        # Browser scrapers: consolidated queries × primary locations
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


def _filter_new_jobs(jobs: List[Job], seen: dict, run_date: str,
                     max_age_days: int = 7) -> List[Job]:
    """Filter out recently-seen jobs. Jobs older than max_age_days are re-evaluated.

    This prevents the seen_jobs filter from permanently blocking jobs that
    didn't match on first encounter but might match after profile changes.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=max_age_days)).isoformat()[:10]

    # Prune entries older than max_age_days
    expired = [k for k, v in seen.items() if v.get("first_seen", "") < cutoff]
    for k in expired:
        del seen[k]
    if expired:
        logger.info(f"Pruned {len(expired)} expired entries from seen_jobs (older than {max_age_days} days)")

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
    """Rank jobs by keyword relevance + recency + geo preference.

    Geo weighting: Ireland 80%, India 15%, US/other 5%.
    Ireland jobs get a massive bonus so they fill most of the max_jobs cutoff.
    India jobs must mention remote. US jobs need sponsorship signals.
    """
    from datetime import datetime, timedelta

    # Build keyword set from config
    target_roles = set()
    for resume_info in config.get("resumes", {}).values():
        for role in resume_info.get("target_roles", []):
            for word in role.lower().split():
                if len(word) > 2:
                    target_roles.add(word)

    for query in config.get("search", {}).get("queries", []):
        for word in query.lower().split():
            if len(word) > 2:
                target_roles.add(word)

    def _detect_geo(job: Job) -> str:
        """Detect which geo region a job belongs to."""
        loc = job.location.lower()
        title_desc = (job.title + " " + (job.description or "")).lower()
        if any(w in loc for w in ["dublin", "ireland", "cork", "galway", "limerick", "waterford"]):
            return "ireland"
        if any(w in loc for w in ["india", "bangalore", "bengaluru", "mumbai", "hyderabad",
                                   "pune", "delhi", "chennai", "noida", "gurgaon", "gurugram"]):
            return "india"
        if any(w in loc for w in ["us", "usa", "united states", "san francisco", "new york",
                                   "seattle", "austin", "chicago", "boston"]):
            return "us"
        # Check description for India/US clues
        if any(w in title_desc for w in ["india", "inr", "lpa", "bangalore", "mumbai"]):
            return "india"
        return "other"

    def score_job(job: Job) -> float:
        """Local relevance score. Higher = more relevant."""
        score = 0.0
        title_lower = job.title.lower()
        desc_lower = job.description.lower() if job.description else ""

        # Title keyword matches (most important)
        title_words = set(title_lower.split())
        title_matches = len(title_words & target_roles)
        score += title_matches * 15

        # Description keyword matches
        if desc_lower:
            desc_matches = sum(1 for kw in target_roles if kw in desc_lower)
            score += min(desc_matches * 3, 20)

        # Geo preference bonus (Ireland >> India > US > other)
        geo = _detect_geo(job)
        loc = job.location.lower()

        if geo == "ireland":
            score += 40  # Strong preference for Ireland
            if "dublin" in loc:
                score += 10
        elif geo == "india":
            # India: must be remote
            if job.remote or "remote" in loc or "remote" in title_lower:
                score += 15
            else:
                score -= 50  # Penalize non-remote India roles heavily
        elif geo == "us":
            # US: must be remote (no authorization)
            if job.remote or "remote" in loc or "remote" in title_lower:
                score += 5
            else:
                score -= 50
        else:
            if "remote" in loc:
                score += 10

        # Recency bonus (strong gradient: today >> yesterday >> 3 days >> week)
        if job.posted_date:
            try:
                posted = datetime.fromisoformat(job.posted_date.replace("Z", "+00:00"))
                days_old = (datetime.now(posted.tzinfo) - posted).days if posted.tzinfo else 0
                if days_old <= 1:
                    score += 25
                elif days_old <= 2:
                    score += 15
                elif days_old <= 3:
                    score += 10
                elif days_old <= 5:
                    score += 5
                # 6-7 days: no bonus, but not penalized
            except (ValueError, TypeError):
                pass

        # Experience level preference: junior/mid >> senior
        # Only boost junior/graduate if combined with tech keywords
        _tech_words = {"engineer", "developer", "devops", "sre", "software", "cloud",
                       "infrastructure", "platform", "backend", "fullstack", "full-stack",
                       "data", "python", "reliability"}
        has_tech = bool(set(title_lower.split()) & _tech_words)
        if any(w in title_lower for w in ["junior", "graduate", "entry", "jr.", "jr "]):
            score += 20 if has_tech else -10  # Non-tech graduate roles get penalized
        elif "senior" in title_lower or "sr." in title_lower or "sr " in title_lower:
            score -= 10  # Deprioritize but don't exclude
        elif "lead" in title_lower or "staff" in title_lower:
            score -= 20

        # Has description bonus
        if len(desc_lower) > 100:
            score += 5

        return score

    return sorted(jobs, key=score_job, reverse=True)


# ── Quick-Reject Pre-Filter ────────────────────────────────────────────

_REJECT_TITLE_PATTERNS = [
    "director", "vice president", "vp ", "vp,", "chief ",
    "principal architect", "staff engineer",
    "distinguished", "fellow",
]
# Removed: "head of" — too broad, catches relevant roles like "Head of Platform"

_REJECT_DESC_PATTERNS = [
    "security clearance required", "ts/sci", "top secret",
    "10+ years", "12+ years", "15+ years",
]
# Removed: "7+ years" and "8+ years" — some of these roles accept strong juniors


def _quick_reject(jobs: List[Job]) -> List[Job]:
    """Filter out jobs that are obviously not a match.

    Removes: Senior Director+, security clearance, 10+ years required.
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


def run_pipeline(context: PipelineContext, dry_run: bool = False, scrape_only: bool = False):
    """Execute the full pipeline.

    Parameters
    ----------
    context:
        PipelineContext wrapping user profile, resumes, search config,
        AI client, and config dict. Created via ``PipelineContext.from_config()``
        for single-user mode or ``PipelineContext.from_db()`` for multi-tenant.
    dry_run:
        If True, scrape + match only — no resume generation.
    scrape_only:
        If True, just scrape and dump results.
    """
    config = context.config
    run_date = context.run_date or datetime.now().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  JOB AUTOMATION PIPELINE — {run_date} {run_time}")
    print(f"{'='*60}\n")

    # Record run start in DB (multi-tenant mode)
    run_record = None
    if context.db:
        try:
            from datetime import date as _date
            run_record = context.db.start_run(context.user.id, _date.fromisoformat(run_date))
            logger.info(f"[DB] Pipeline run {run_record['run_id']} started for user {context.user.id}")
        except Exception as e:
            logger.warning(f"[DB] Failed to record run start: {e}")

    # Create daily output directory using context properties
    base_dir = context.output_dir
    daily_dir = context.daily_dir
    resumes_dir = context.resumes_dir
    coverletters_dir = context.coverletters_dir

    # --- Step 1: Initialize scrapers ---
    logger.info("Initializing scrapers...")
    scrapers = init_scrapers(config)
    if not scrapers:
        logger.critical("No scrapers initialized. Check API keys in config.yaml")
        sys.exit(1)
    logger.info(f"Active scrapers: {[s.name for s in scrapers]}")

    # --- Step 2: Scrape jobs ---
    # Merge user-specific search config (from DB) over config.yaml defaults
    # so multi-tenant users can override queries, locations, and days_back.
    scrape_config = dict(config)
    if context.search_config:
        merged_search = dict(config.get("search", {}))
        if "queries" in context.search_config:
            merged_search["queries"] = context.search_config["queries"]
        if "locations" in context.search_config:
            merged_search["locations"] = context.search_config["locations"]
        if "days_back" in context.search_config:
            merged_search["days_back"] = context.search_config["days_back"]
        scrape_config["search"] = merged_search

    logger.info("Scraping jobs...")
    raw_jobs = scrape_all_jobs(scrapers, scrape_config)
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
    resumes = context.resumes
    if not resumes:
        logger.critical("No resumes loaded. Check paths in config.yaml or user resume settings")
        sys.exit(1)

    ai_client = context.ai_client

    max_jobs = context.search_config.get("max_jobs_per_run", config["search"].get("max_jobs_per_run", 20))
    min_score = context.search_config.get("min_match_score", config["search"].get("min_match_score", 60))

    # Rank by local relevance BEFORE cutoff (best jobs first)
    ranked_jobs = _rank_jobs_locally(filtered_jobs, config)
    jobs_to_match = ranked_jobs[:max_jobs]
    logger.info(f"Matching {len(jobs_to_match)} jobs (max_per_run: {max_jobs}, from {len(filtered_jobs)} candidates)...")

    matched_jobs = match_jobs(
        jobs=jobs_to_match,
        resumes=resumes,
        ai_client=ai_client,
        min_score=min_score,
        user_profile=context.user,
    )
    logger.info(f"Matched jobs (avg >= {min_score}): {len(matched_jobs)}")

    # Update seen_jobs with match scores
    for job in matched_jobs:
        if job.job_id in seen_jobs:
            seen_jobs[job.job_id]["score"] = job.match_score
            seen_jobs[job.job_id]["matched"] = True
    _save_seen_jobs(seen_jobs, seen_path)

    # --- Step 4b: Upsert matched jobs to Supabase ---
    if context.db and matched_jobs:
        logger.info(f"[DB] Upserting {len(matched_jobs)} matched jobs to Supabase...")
        upserted = 0
        for job in matched_jobs:
            try:
                row = _job_to_supabase_row(job)
                context.db.upsert_job(context.user.id, row)
                upserted += 1
            except Exception as e:
                logger.warning(f"[DB] Failed to upsert job {job.job_id} ({job.title}): {e}")
        logger.info(f"[DB] Upserted {upserted}/{len(matched_jobs)} jobs to Supabase")

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
    #
    # Two paths:
    #   A) Google Docs — if the resume config has a google_doc_id, use
    #      plain-text tailoring + Google Docs template cloning + PDF export.
    #   B) LaTeX fallback — original flow for resumes without google_doc_id.
    google_docs_cfg = config.get("google_docs", {})
    gdocs_enabled = google_docs_cfg.get("enabled", False)
    gdocs_creds = google_docs_cfg.get("credentials_path", "google_credentials.json")
    gdocs_share = google_docs_cfg.get("share_with", "")

    logger.info("Tailoring resumes + scoring to 85+ (ATS, HM, TR)...")
    for job in matched_jobs:
        base_tex = resumes.get(job.matched_resume, "")
        if not base_tex:
            logger.info(f"Skipping {job.title} @ {job.company}: no base resume for profile '{job.matched_resume}'")
            continue

        logger.info(f"--- {job.title} @ {job.company} ---")
        logger.info(f"Base scores: ATS={job.ats_score} HM={job.hiring_manager_score} TR={job.tech_recruiter_score}")

        # Preserve initial match scores before tailoring overwrites them
        job.initial_ats_score = job.ats_score
        job.initial_hm_score = job.hiring_manager_score
        job.initial_tr_score = job.tech_recruiter_score
        job.initial_match_score = job.match_score

        # Check if this resume type has a Google Doc template
        resume_cfg = config.get("resumes", {}).get(job.matched_resume, {})
        google_doc_id = resume_cfg.get("google_doc_id", "")

        if gdocs_enabled and google_doc_id:
            # ── Path A: Google Docs ──────────────────────────────────
            logger.info(f"[GDOCS] Using Google Docs template {google_doc_id}")

            # 1. Extract base sections from LaTeX as plain text
            base_sections = extract_base_sections(base_tex, job.matched_resume)

            # 2. Tailor sections using AI (plain text, not LaTeX)
            tailored_sections = tailor_resume_text(
                job=job,
                base_sections=base_sections,
                ai_client=ai_client,
                user_profile=context.user,
            )

            # 3. Score and improve (text mode)
            improved_sections, scores = score_and_improve(
                tailored_tex="",  # unused in text mode
                job=job,
                ai_client=ai_client,
                min_score=80,
                max_rounds=1,
                text_mode=True,
                sections=tailored_sections,
            )

            # Update with post-tailoring scores
            job.ats_score = scores.get("ats_score", 0)
            job.hiring_manager_score = scores.get("hiring_manager_score", 0)
            job.tech_recruiter_score = scores.get("tech_recruiter_score", 0)
            job.match_score = round((job.ats_score + job.hiring_manager_score + job.tech_recruiter_score) / 3, 1)

            # 4. Create Google Doc from template + export PDF
            safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
            safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
            date_str = datetime.now().strftime("%Y-%m-%d")
            name_prefix = context.user.safe_filename_prefix()
            pdf_filename = f"{name_prefix}_{safe_title}_{safe_company}_{date_str}".replace(" ", "_")
            pdf_path = str(resumes_dir / f"{pdf_filename}.pdf")
            doc_title = f"Resume – {job.title} at {job.company} ({date_str})"

            try:
                result = google_docs_client.create_resume_doc(
                    template_doc_id=google_doc_id,
                    replacements=improved_sections,
                    title=doc_title,
                    output_pdf_path=pdf_path,
                    share_with=gdocs_share,
                    credentials_path=gdocs_creds,
                )
                job.tailored_pdf_path = result.get("pdf_path", "")
                job.resume_doc_url = result.get("doc_url", "")
                logger.info(f"[GDOCS] Resume created: {result.get('doc_id', '')} -> {Path(pdf_path).name}")
            except Exception as e:
                logger.error(f"[GDOCS] Failed to create resume doc for {job.company}: {e}")
                # Fall through — job.tailored_pdf_path stays empty
        else:
            # ── Path B: LaTeX fallback ───────────────────────────────
            # First pass: tailor the resume using match data
            tailor_resume(
                job=job,
                base_tex=base_tex,
                ai_client=ai_client,
                output_dir=resumes_dir,
                user_profile=context.user,
            )

            # Second pass: re-score the tailored version and improve until 85+
            if job.tailored_tex_path and Path(job.tailored_tex_path).exists():
                tailored_tex = Path(job.tailored_tex_path).read_text(encoding="utf-8")

                improved_tex, scores = score_and_improve(
                    tailored_tex=tailored_tex,
                    job=job,
                    ai_client=ai_client,
                    min_score=80,
                    max_rounds=1,
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
    # TODO: Add Google Docs cover letter template support once a template
    #       is created. For now, all cover letters use the LaTeX path.
    #       When ready, use generate_cover_letter_doc() from cover_letter.py
    #       with a cover_letter_template_doc_id from config.
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
            user_profile=context.user,
        )

    # --- Step 8: Compile LaTeX → PDF ---
    # Only compile .tex files for jobs that used the LaTeX path.
    # Jobs that used Google Docs already have their PDFs from Step 5.
    logger.info("Compiling LaTeX to PDF...")
    for job in matched_jobs:
        if job.tailored_tex_path:
            # LaTeX path — compile .tex to .pdf
            pdf = compile_tex_to_pdf(job.tailored_tex_path)
            job.tailored_pdf_path = pdf
        # Google Docs resumes already have tailored_pdf_path set (no .tex file)
        if job.cover_letter_tex_path:
            pdf = compile_tex_to_pdf(job.cover_letter_tex_path)
            job.cover_letter_pdf_path = pdf

    # --- Step 8b: Upload PDFs to S3 (if configured) ---
    if os.environ.get("S3_BUCKET_NAME"):
        logger.info("Uploading artifacts to S3...")
        s3_urls = upload_artifacts(matched_jobs, run_date)
        for job in matched_jobs:
            urls = s3_urls.get(job.job_id, {})
            job.resume_s3_url = urls.get("resume_url", "")
            job.cover_letter_s3_url = urls.get("cover_letter_url", "")
    else:
        logger.info("S3 upload skipped (S3_BUCKET_NAME not set)")

    # --- Step 8c: Upload PDFs to Google Drive (if configured) ---
    gdrive_config = config.get("google_drive", {})
    gdrive_creds = gdrive_config.get("credentials_path", "google_credentials.json")
    if gdrive_config.get("enabled") and Path(gdrive_creds).exists():
        logger.info("Uploading artifacts to Google Drive...")
        try:
            drive_urls = drive_upload_artifacts(
                matched_jobs, run_date,
                credentials_path=gdrive_creds,
                share_with=gdrive_config.get("share_with", ""),
                root_folder_id=gdrive_config.get("folder_id", ""),
            )
            for job in matched_jobs:
                urls = drive_urls.get(job.job_id, {})
                job.resume_drive_url = urls.get("resume_drive_url", "")
                job.cover_letter_drive_url = urls.get("cover_letter_drive_url", "")
        except Exception as e:
            logger.warning(f"Google Drive upload failed (continuing without): {e}")
    else:
        logger.info("Google Drive upload skipped (not configured or credentials missing)")

    # --- Step 8d: Final Supabase upsert (with enriched data) ---
    # Re-upsert jobs now that they have post-tailoring scores, Drive URLs,
    # contacts, and PDF paths.  This overwrites the initial Step 4b insert.
    if context.db and matched_jobs:
        logger.info(f"[DB] Final upsert of {len(matched_jobs)} enriched jobs to Supabase...")
        for job in matched_jobs:
            try:
                row = _job_to_supabase_row(job)
                # Include artifact URLs that are now populated
                if job.resume_doc_url:
                    row["resume_doc_url"] = job.resume_doc_url
                if job.resume_s3_url:
                    row["resume_s3_url"] = job.resume_s3_url
                if job.cover_letter_s3_url:
                    row["cover_letter_s3_url"] = job.cover_letter_s3_url
                context.db.upsert_job(context.user.id, row)
            except Exception as e:
                logger.warning(f"[DB] Failed to upsert enriched job {job.job_id}: {e}")

    # --- Step 9: Update master Excel tracker ---
    logger.info("Updating master Excel tracker...")
    tracker_path = base_dir / config["output"]["tracker_filename"]
    create_or_update_tracker(matched_jobs, str(tracker_path), run_date)

    # --- Step 9b: Upload tracker to S3 ---
    tracker_url = None
    if os.environ.get("S3_BUCKET_NAME"):
        tracker_url = upload_tracker(str(tracker_path), run_date)
        if tracker_url:
            logger.info(f"Tracker available at S3 (30-day link)")

    # --- Step 9c: Upload tracker to Google Drive ---
    drive_tracker_url = None
    if gdrive_config.get("enabled") and Path(gdrive_creds).exists():
        try:
            drive_tracker_url = drive_upload_tracker(
                str(tracker_path), run_date,
                credentials_path=gdrive_creds,
                share_with=gdrive_config.get("share_with", ""),
                root_folder_id=gdrive_config.get("folder_id", ""),
            )
            if drive_tracker_url:
                logger.info(f"Tracker available on Google Drive")
        except Exception as e:
            logger.warning(f"Google Drive tracker upload failed (continuing without): {e}")

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

    # Build detailed per-scraper stats for diagnostics and self-improvement
    scraper_stats = {}

    # Phase 1: Count raw jobs returned per source + collect latency
    for job in raw_jobs:
        src = getattr(job, "source", "unknown")
        if src not in scraper_stats:
            scraper_stats[src] = {
                "jobs_returned": 0,
                "jobs_after_dedup": 0,
                "jobs_matched": 0,
                "match_rate": 0,
                "errors": 0,
                "avg_match_score": 0,
                "latency_seconds": 0,
                "_scores": [],
                "_latencies": [],
            }
        scraper_stats[src]["jobs_returned"] += 1
        scrape_time = getattr(job, "_scrape_time", None)
        if scrape_time is not None:
            scraper_stats[src]["_latencies"].append(scrape_time)

    # Phase 2: Count jobs that survived dedup per source
    for job in unique_jobs:
        src = getattr(job, "source", "unknown")
        if src in scraper_stats:
            scraper_stats[src]["jobs_after_dedup"] += 1

    # Phase 3: Count matched jobs per source + collect match scores
    for job in matched_jobs:
        src = getattr(job, "source", "unknown")
        if src in scraper_stats:
            scraper_stats[src]["jobs_matched"] += 1
            scraper_stats[src]["_scores"].append(job.match_score)

    # Compute derived metrics and clean up temporary fields
    for src, stats in scraper_stats.items():
        returned = stats["jobs_returned"]
        matched = stats["jobs_matched"]
        stats["match_rate"] = round(matched / returned, 3) if returned > 0 else 0

        scores = stats.pop("_scores")
        stats["avg_match_score"] = round(sum(scores) / len(scores), 1) if scores else 0

        latencies = stats.pop("_latencies")
        stats["latency_seconds"] = round(max(latencies), 1) if latencies else 0

    # Backward compat: keep "count" alias for anything reading old format
    for src, stats in scraper_stats.items():
        stats["count"] = stats["jobs_returned"]

    # Save run metadata (enriched for self-improvement analysis)
    meta = {
        "run_date": run_date,
        "run_time": run_time,
        "jobs_scraped": len(raw_jobs),
        "jobs_unique": len(unique_jobs),
        "jobs_matched": len(matched_jobs),
        "jobs_above_85": all_85_count,
        "resumes_generated": resumes_generated,
        "cover_letters_generated": cls_generated,
        "scraper_stats": scraper_stats,
        "matched_jobs": [
            {
                "title": j.title,
                "company": j.company,
                "description": getattr(j, "description", "")[:500],
                "score": j.match_score,
                "ats_score": getattr(j, "ats_score", 0),
                "hiring_manager_score": getattr(j, "hiring_manager_score", 0),
                "tech_recruiter_score": getattr(j, "tech_recruiter_score", 0),
                "resume_type": j.matched_resume,
                "apply_url": j.apply_url,
            }
            for j in matched_jobs
        ],
    }
    meta_path = daily_dir / "run_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # --- Self-improvement analysis ---
    try:
        from self_improver import run_self_improvement
        improvement_report = run_self_improvement(str(daily_dir))
        logger.info("Self-improvement analysis complete: %d findings, %d suggestions",
                    len(improvement_report.get("findings", [])),
                    len(improvement_report.get("suggestions", [])))
    except Exception as e:
        logger.warning("Self-improvement analysis failed: %s", e)

    # --- Record run completion in DB (multi-tenant mode) ---
    if context.db and run_record:
        try:
            context.db.complete_run(run_record["run_id"], {
                "raw_jobs": len(raw_jobs),
                "unique_jobs": len(unique_jobs),
                "matched_jobs": len(matched_jobs),
                "resumes_generated": resumes_generated,
                "cover_letters_generated": cls_generated,
                "jobs_above_85": all_85_count,
            })
            logger.info(f"[DB] Pipeline run {run_record['run_id']} completed")
        except Exception as e:
            logger.warning(f"[DB] Failed to record run completion: {e}")

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
            tracker_path=str(tracker_path),
            tracker_url=tracker_url,
            drive_tracker_url=drive_tracker_url,
        )
    else:
        logger.info("Email skipped (set GMAIL_ADDRESS + GMAIL_APP_PASSWORD to enable)")


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(description="Job Automation Pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + match only, no generation")
    parser.add_argument("--scrape-only", action="store_true", help="Just scrape and show results")
    parser.add_argument("--user-id", help="Run pipeline for a specific user (requires Supabase)")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.user_id:
        from db_client import SupabaseClient
        db = SupabaseClient.from_env()
        context = PipelineContext.from_db(args.user_id, db, config)
        logger.info(f"Running pipeline for user {args.user_id} ({context.user.name})")
    else:
        context = PipelineContext.from_config(config)

        # In single-user mode, optionally attach a Supabase client so
        # matched jobs and run stats are persisted to the cloud DB.
        # Gracefully skipped if SUPABASE_URL / SUPABASE_SERVICE_KEY are not set.
        if context.db is None:
            db = _try_init_supabase()
            if db:
                context.db = db
                # Resolve a user_id for writes (env var > config > email hash)
                user_id = _resolve_user_id(config)
                # Patch the user profile id to match what we'll write to Supabase
                context.user.id = user_id
                logger.info(f"[DB] Supabase enabled for single-user mode (user_id={user_id})")

    run_pipeline(context, dry_run=args.dry_run, scrape_only=args.scrape_only)


if __name__ == "__main__":
    main()
