"""Merge and deduplicate scraped jobs, apply relevance pre-filter.

Reads from jobs_raw (shared pool) and scrape_runs (output contract).
Applies 3-tier dedup (exact hash + exact company+title + fuzzy) and
relevance pre-filter before passing job hashes to score_batch.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Pre-filter: seniority keywords that indicate too-senior roles
REJECT_TITLE_KEYWORDS = {"director", "vp", "vice president", "head of", "chief", "principal architect", "cto", "cio"}

# Pre-filter: minimum tech skill keywords to match against JD
DEFAULT_USER_SKILLS = {
    "python", "aws", "kubernetes", "docker", "terraform", "react", "typescript",
    "node", "fastapi", "linux", "ci/cd", "devops", "sre", "cloud", "java",
    "javascript", "golang", "go", "microservices", "api",
}


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _normalize_title(title: str) -> str:
    """Strip seniority prefixes for fuzzy matching."""
    title = title.lower().strip()
    for prefix in ("senior ", "junior ", "lead ", "staff ", "principal ", "sr. ", "jr. "):
        title = title.replace(prefix, "")
    # Strip Roman numeral suffixes
    title = re.sub(r'\s+(i{1,3}|iv|v)\s*$', '', title)
    return title.strip()


def _fuzzy_match(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are similar enough."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


def _extract_tech_keywords(text: str) -> set:
    """Extract tech keywords from job description using regex."""
    text = text.lower()
    found = set()
    tech_patterns = [
        "python", "java", "javascript", "typescript", "golang", "go", "rust", "c\\+\\+",
        "ruby", "php", "scala", "kotlin", "swift", "react", "angular", "vue",
        "node\\.?js", "django", "flask", "fastapi", "spring", "express",
        "aws", "azure", "gcp", "google cloud", "kubernetes", "k8s", "docker",
        "terraform", "ansible", "jenkins", "ci/cd", "github actions",
        "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "linux", "devops", "sre", "site reliability", "microservices", "api",
        "machine learning", "ml", "ai", "deep learning", "nlp",
    ]
    for pattern in tech_patterns:
        if re.search(r'\b' + pattern + r'\b', text):
            found.add(pattern.replace("\\", "").replace(".?", ""))
    return found


def _richness_score(job: dict) -> tuple:
    """Score a job dict by data richness for tie-breaking during dedup.

    Returns a tuple (desc_len, field_count, last_seen) so that max() picks
    the version with the longest description, most populated fields, and
    most recent scrape timestamp.
    """
    desc_len = len(job.get("description", "") or "")
    field_count = sum(1 for v in job.values() if v is not None and v != "")
    last_seen = job.get("last_seen", "") or job.get("scraped_at", "") or ""
    return (desc_len, field_count, last_seen)


def should_skip_cross_run(existing_job: dict | None, max_age_days: int = 7) -> bool:
    """Check if job was scored recently enough to skip re-scoring."""
    if not existing_job:
        return False
    scored_at = existing_job.get("scored_at")
    if not scored_at:
        return False
    scored_dt = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
    return (datetime.now(scored_dt.tzinfo) - scored_dt) < timedelta(days=max_age_days)


def cross_run_check(existing_job: dict | None, max_age_days: int = 7) -> dict:
    """Check if job was recently processed. Returns reuse instructions."""
    if not existing_job or not should_skip_cross_run(existing_job, max_age_days):
        return {"skip_scoring": False, "skip_tailoring": False, "reuse_artifacts": {}}
    return {
        "skip_scoring": True,
        "skip_tailoring": True,
        "reuse_artifacts": {
            "base_ats_score": existing_job.get("base_ats_score"),
            "base_hm_score": existing_job.get("base_hm_score"),
            "base_tr_score": existing_job.get("base_tr_score"),
            "tailored_ats_score": existing_job.get("tailored_ats_score"),
            "tailored_hm_score": existing_job.get("tailored_hm_score"),
            "tailored_tr_score": existing_job.get("tailored_tr_score"),
            "resume_s3_url": existing_job.get("resume_s3_url"),
            "cover_letter_s3_url": existing_job.get("cover_letter_s3_url"),
            "writing_quality_score": existing_job.get("writing_quality_score"),
        },
    }


def _prefilter_job(job: dict, user_skills: set) -> tuple[bool, str]:
    """Apply relevance pre-filter. Returns (pass, reason)."""
    title = (job.get("title") or "").lower()
    desc = job.get("description") or ""
    location = (job.get("location") or "").lower()

    # Rule 1: Seniority filter
    for kw in REJECT_TITLE_KEYWORDS:
        if kw in title:
            return False, f"too_senior:{kw}"

    # Rule 2: Description quality gate
    if len(desc) < 100:
        return False, "description_too_short"

    # Rule 3: Minimum skill overlap
    jd_skills = _extract_tech_keywords(desc)
    overlap = jd_skills & user_skills
    if len(overlap) < 2:
        return False, f"skill_overlap:{len(overlap)}"

    # Rule 4: Location compatibility (basic)
    incompatible_in_office = {"india", "bangalore", "mumbai", "hyderabad", "pune", "chennai"}
    if any(loc in location for loc in incompatible_in_office) and "remote" not in location:
        return False, "incompatible_location:india_in_office"

    return True, "pass"


def handler(event, context):
    db = get_supabase()
    pipeline_run_id = event.get("pipeline_run_id", "")
    user_id = event.get("user_id", "")
    today = datetime.now(timezone.utc).date().isoformat()

    # --- Source 1: Read from scrape_runs (Fargate tasks) ---
    fargate_hashes = []
    if pipeline_run_id:
        runs = db.table("scrape_runs").select("source, status, new_job_hashes") \
            .eq("pipeline_run_id", pipeline_run_id).execute()
        for run in (runs.data or []):
            if run.get("new_job_hashes"):
                fargate_hashes.extend(run["new_job_hashes"])
        logger.info(f"[merge_dedup] scrape_runs: {len(fargate_hashes)} hashes from Fargate tasks")

    # --- Source 2: Get today's scraped jobs from jobs_raw ---
    result = db.table("jobs_raw").select("job_hash, title, company, source, description, location") \
        .gte("scraped_at", today).execute()

    all_jobs = result.data or []

    # --- Source 3: Catch unscored jobs from recent days (backfill) ---
    # If the pipeline failed mid-run or was offline, jobs sit in jobs_raw
    # but never make it to the scored 'jobs' table. Pick them up within 7 days.
    lookback = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    recent = db.table("jobs_raw").select("job_hash, title, company, source, description, location") \
        .gte("scraped_at", lookback).lt("scraped_at", today).execute()
    if recent.data:
        today_hashes = {j["job_hash"] for j in all_jobs}
        backfill = [j for j in recent.data if j["job_hash"] not in today_hashes]
        if backfill:
            all_jobs.extend(backfill)
            logger.info(f"[merge_dedup] Backfill: {len(backfill)} unscored jobs from last 7 days")

    if not all_jobs and not fargate_hashes:
        return {"new_job_hashes": [], "total_new": 0, "filtered_out": 0}

    # If we have specific hashes from this pipeline run, scope to those
    # This prevents processing stale jobs from previous runs
    if fargate_hashes:
        fargate_set = set(fargate_hashes)
        scoped = [j for j in all_jobs if j["job_hash"] in fargate_set]
        if scoped:
            all_jobs = scoped
            logger.info(f"[merge_dedup] Scoped to {len(all_jobs)} jobs from this pipeline run")

    # --- Tier 1: Exact hash dedup (already done during scraping, but verify) ---
    by_hash = {}
    for job in all_jobs:
        h = job["job_hash"]
        existing = by_hash.get(h)
        if not existing or _richness_score(job) > _richness_score(existing):
            by_hash[h] = job

    # --- Tier 0: Exact normalized (company+title) dedup (cross-source) ---
    # Same job from LinkedIn and Indeed has different descriptions → different job_hash.
    # This tier catches those by ignoring description entirely.
    from utils.canonical_hash import normalize_company, normalize_whitespace
    by_dedup_key = {}
    for job in by_hash.values():
        norm_company = normalize_company(job.get("company", ""))
        norm_title = normalize_whitespace(job.get("title", "")).lower()
        dedup_key = f"{norm_company}|{norm_title}"
        existing = by_dedup_key.get(dedup_key)
        if not existing or _richness_score(job) > _richness_score(existing):
            by_dedup_key[dedup_key] = job

    logger.info(f"[merge_dedup] Tier 0: {len(by_hash)} → {len(by_dedup_key)} (exact company+title)")

    # --- Tier 2: Fuzzy title+company dedup (catches remaining near-matches) ---
    seen_fuzzy = {}
    for job in by_dedup_key.values():
        norm_title = _normalize_title(job.get("title", ""))
        norm_company = job.get("company", "").lower().strip()
        fuzzy_key = f"{norm_company}|{norm_title}"

        matched = False
        for existing_key, existing_job in seen_fuzzy.items():
            ex_company, ex_title = existing_key.split("|", 1)
            if _fuzzy_match(norm_company, ex_company, 0.75) and _fuzzy_match(norm_title, ex_title, 0.65):
                # Keep the richest version
                if _richness_score(job) > _richness_score(existing_job):
                    seen_fuzzy[existing_key] = job
                matched = True
                break

        if not matched:
            seen_fuzzy[fuzzy_key] = job

    unique_jobs = list(seen_fuzzy.values())

    # --- Pre-filter: relevance check ---
    # Load user skills (from search config or defaults)
    user_skills = DEFAULT_USER_SKILLS
    if user_id:
        try:
            config = db.table("user_search_configs").select("queries").eq("user_id", user_id).execute()
            if config.data and config.data[0].get("queries"):
                # Add user's search queries as additional skill signals
                for q in config.data[0]["queries"]:
                    user_skills |= {w.lower() for w in q.split() if len(w) > 2}
        except Exception:
            pass

    filtered_jobs = []
    filtered_out = 0
    for job in unique_jobs:
        passes, reason = _prefilter_job(job, user_skills)
        if passes:
            filtered_jobs.append(job)
        else:
            filtered_out += 1
            logger.debug(f"[pre-filter] Rejected: {job.get('title')} — {reason}")

    # --- Check which are truly new (not already scored for this user) ---
    existing_hashes = set()
    if user_id:
        existing = db.table("jobs").select("job_hash").eq("user_id", user_id) \
            .not_.is_("job_hash", "null").execute()
        existing_hashes = {j["job_hash"] for j in (existing.data or [])}

    new_hashes = [j["job_hash"] for j in filtered_jobs if j["job_hash"] not in existing_hashes]

    logger.info(
        f"[merge_dedup] {len(all_jobs)} scraped → {len(unique_jobs)} unique "
        f"→ {len(filtered_jobs)} passed filter ({filtered_out} filtered) "
        f"→ {len(new_hashes)} new for scoring"
    )
    return {"new_job_hashes": new_hashes, "total_new": len(new_hashes), "filtered_out": filtered_out}
