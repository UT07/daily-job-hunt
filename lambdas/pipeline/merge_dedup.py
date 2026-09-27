"""Merge and deduplicate scraped jobs, apply relevance pre-filter.

Reads from jobs_raw (shared pool) and scrape_runs (output contract).
Applies 3-tier dedup (exact hash + exact company+title + fuzzy) and
relevance pre-filter (incl. freshness) before passing job hashes to
score_batch.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy SSM client — boto3.client at module load forces AWS_DEFAULT_REGION
# on every importer (including unit tests + runtime-import smoke). Same
# pattern as ai_helper.py shipped in PR #23.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm

# Pre-filter: title keywords that mark a role as either too senior or the
# wrong function for this pipeline's two IC resume archetypes (sre_devops,
# fullstack — confirmed via the live /api/health "resumes_loaded" field;
# there is no manager/data-science/sales-engineering archetype to tailor
# against, so a JD in one of those tracks cannot produce a good tailored
# resume regardless of how well its buzzwords overlap with DEFAULT_USER_SKILLS).
#
# Tuned 2026-09-25 against the full 12,672-row jobs_raw corpus
# (scripts/tune_prefilter.py) — every addition below was checked against its
# real matching titles in that corpus before being kept; see
# .superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/fix-throughput-report.md
# for the sample review. Two findings drove most of this:
#   - "architect" (see _WORD_BOUNDARY_REJECT_PATTERN below, not this set):
#     all 105 admitted titles containing it were Solutions/Partner/Presales/
#     Cloud-Solutions/Professional-Services Architect (pre-sales or
#     consulting) or Staff/Principal-tier — zero were a reachable hands-on
#     IC role. Subsumes the old "principal architect".
#   - "manager": all 383 admitted titles containing it, INCLUDING the
#     "Engineering Manager" / "Manager, Site Reliability Engineering" ones,
#     are a people-management track this pipeline has no resume archetype
#     for — an IC-only base resume does not tailor into a credible EM
#     resume just because the JD is SRE-flavored.
# "sales" and "partner" are deliberately NOT bare keywords: "sales" is a
# substring of "Salesforce" (a real platform-engineering title observed in
# the corpus), and "partner" matched "Staff Security Engineer, Security
# Partnerships" (a real IC security role). Scoped phrases below avoid both.
REJECT_TITLE_KEYWORDS = {
    # Seniority tiers unreachable for a graduate/mid-level IC candidate.
    "director", "vp", "vice president", "head of", "chief", "cto", "cio",
    "distinguished",
    # Management track — no resume archetype exists for it (see above).
    "manager",
    # Sales / GTM / pre-sales / customer-facing technical roles — JD
    # boilerplate mentions the same stack as an IC role next to it, but the
    # job itself is not hands-on engineering.
    "sales engineer", "sales specialist", "sales operations", "account executive",
    "business development", "solutions consultant", "presales", "pre-sales",
    "professional services",
    # Other roles this pipeline's two archetypes cannot credibly tailor
    # toward, or that the user has explicitly ruled out.
    "developer advocate", "data scientist", "recruiter", "compensation analyst",
    "controller", "revenue analytics", "revenue technology", "revenue technical",
    # Not a job posting at all — observed verbatim on a real Greenhouse board:
    # "2026 - Women in Tech Summit, EMEA".
    "summit",
}

# Keywords that need word-boundary matching, not the plain substring check
# REJECT_TITLE_KEYWORDS uses above, because their bare substring collides
# with real words that show up in genuine on-target titles:
#   - "intern" is a substring of "International" and "Internal" — both
#     observed on real, senior, on-target titles ("Staff Software Engineer,
#     International", "...Data Platforms & Internal Tooling"). Rejecting
#     internships at all matches existing user feedback (feedback_graduated.md
#     — the user has already graduated).
#   - "architect" is a substring of "Architecture" — caught 2 real backend/SRE
#     titles that only mention "Architecture" as an org/team name ("Senior
#     Backend Engineer, Architecture Engineering: Nonlinear Productivity",
#     "Site Reliability Engineer - Video Live Streaming Architecture") before
#     this was moved out of the plain-substring set above.
# Every other collision this same corpus-scan checked for ("manager",
# "sales engineer", "partner", the multi-word phrases) came back clean — see
# the throughput fix report for the specific counts.
_WORD_BOUNDARY_REJECT_PATTERN = re.compile(
    r"\b(intern|interns|internship|architect|architects)\b", re.IGNORECASE
)

# Freshness pre-filter — drop jobs whose posted_date is older than this many
# days at scrape time. Configurable via JOB_MAX_AGE_DAYS env var. Default 14:
# strikes a balance between "first-mover advantage" (the user's goal: apply
# ASAP) and tolerating sources that update posted_date only weekly. Jobs
# where posted_date is None pass through this filter unchanged — we don't
# want to throw away rows just because the source didn't supply it.
JOB_MAX_AGE_DAYS = int(os.environ.get("JOB_MAX_AGE_DAYS", "14"))

# How many days back Source 3 (backfill, see handler() below) looks in
# jobs_raw for rows that were scraped but never made it into the scored
# `jobs` table. Was a hardcoded 7 — anything that missed that window was
# lost permanently, not merely delayed (2026-09-25 audit, P0-4): with
# scoring throughput capped well below scrape volume, a job can easily sit
# unscored for longer than a week without anything being wrong.
#
# Widened, not made unbounded. A job's own staleness is already governed
# independently by Rule 0 above (JOB_MAX_AGE_DAYS, keyed off posted_date,
# not scraped_at) — widening how far back we SEARCH for unscored rows does
# not let a genuinely stale posting reach the user, because Rule 0 still
# rejects it either way. What an unbounded window WOULD cost is a
# jobs_raw scan that grows forever (12,672 rows today, more every day this
# pipeline runs), re-fetched on every single invocation. 30 days is
# double JOB_MAX_AGE_DAYS's default and comfortably covers any realistic
# pipeline gap this project has actually had (the current parking is a
# deliberate months-long exception, not something a backfill window should
# be sized around — a gap that long calls for fresh scraping, not
# resurrecting month-old raw rows whose postings are almost certainly gone).
BACKFILL_LOOKBACK_DAYS = int(os.environ.get("BACKFILL_LOOKBACK_DAYS", "30"))

# Hard ceiling on how many jobs one run hands to ScoreBatchMap, regardless of
# how many pass the relevance filter below. Groq's free tier caps AI scoring
# at roughly 80-120 jobs/day (8,000 tokens/minute ≈ 1.4 calls/minute); 150
# leaves headroom without depending entirely on the content filter to hold
# the line on a burst day. On 2026-09-01 (1,427 raw jobs, the single
# biggest day on record) the tuned relevance filter alone still admitted
# more than this on its own — see the throughput fix report. When more than
# MAX_JOBS_PER_RUN pass the relevance filter, keep the best ones (highest
# tech-skill overlap with the user's stack) rather than an arbitrary/
# order-dependent subset — see _job_relevance_rank below.
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "150"))

# Pre-filter: minimum tech skill keywords to match against JD
DEFAULT_USER_SKILLS = {
    "python", "aws", "kubernetes", "docker", "terraform", "react", "typescript",
    "node", "fastapi", "linux", "ci/cd", "devops", "sre", "cloud", "java",
    "javascript", "golang", "go", "microservices", "api",
}


def get_param(name):
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


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


def _job_relevance_rank(job: dict, user_skills: set) -> tuple:
    """Rank a job for the MAX_JOBS_PER_RUN cutoff — higher is better.

    Primary key is skill overlap (the same signal Rule 3 already uses to
    admit/reject, just used here to order rather than gate), tie-broken by
    freshness (lower age wins) so that among equally-relevant jobs the most
    recently posted one survives the cut. Age ties broken to 0 when
    unavailable — never rank a missing-date job LAST by treating "unknown"
    as "old"; Rule 0 already chose not to penalize missing posted_date, and
    ranking should stay consistent with that.
    """
    overlap = len(_extract_tech_keywords(job.get("description") or "") & user_skills)
    age = _job_age_days(job)
    return (overlap, -(age if age is not None else 0.0))


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


def _job_age_days(job: dict, now: datetime | None = None) -> float | None:
    """Days since the job was posted, or None when posted_date is missing.

    Centralised so the freshness rule and any future "fresh-only" sort can
    share the same parsing rules.
    """
    posted = job.get("posted_date")
    if not posted:
        return None
    s = posted.replace("Z", "+00:00") if isinstance(posted, str) else posted
    try:
        dt = datetime.fromisoformat(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def _query_phrases(queries: list) -> frozenset:
    """Turn the user's own search queries into phrase-level match signals:
    consecutive word pairs, e.g. "Site Reliability Engineer" ->
    {"site reliability", "reliability engineer"}. A single-word query keeps
    its one word (nothing to pair it with).

    Phrases, not lone words, on purpose. An earlier version of this
    function matched individual query words directly and was verified
    (2026-09-27, live jobs_raw corpus, ~13.6k rows) to raise the real
    production user's admit rate from 13.3% to 17.4% -- almost entirely
    noise: "full" from "Full Stack Engineer" matching plain "Full Time"/
    "Full-time" employment-type boilerplate, and "engineer" alone matching
    nearly every posting in an all-tech pool. A two-word phrase match is a
    far more specific, low-noise signal than either word alone.
    """
    phrases: set = set()
    for q in queries:
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", q) if len(w) > 2]
        if len(words) == 1:
            phrases.add(words[0])
        for i in range(len(words) - 1):
            phrases.add(f"{words[i]} {words[i + 1]}")
    return frozenset(phrases)


def _query_phrase_overlap(desc_lower: str, query_phrases: frozenset) -> set:
    """Which of the user's own query phrases appear in the JD text.

    Domain-neutral counterpart to `_extract_tech_keywords`, which only
    recognises a closed software vocabulary (python, kubernetes, ...) and
    therefore can never find a match for a non-IT JD -- a nurse's or
    accountant's job description simply doesn't contain any of those
    patterns. This checks the JD against whatever the user actually
    searched for instead, so it works for any profession. Hyphens/slashes
    in the JD ("full-stack", "on-site") are normalised to spaces so they
    still match a space-joined query phrase.
    """
    if not query_phrases:
        return set()
    normalized = re.sub(r"[-/]", " ", desc_lower)
    hits = set()
    for phrase in query_phrases:
        # Escape each word separately, THEN join with \s+ -- re.escape()
        # itself escapes plain spaces (it's conservative about re.VERBOSE),
        # so escaping the whole phrase first and patching afterwards would
        # leave a stray literal backslash in the pattern instead of \s+.
        pattern = r"\s+".join(re.escape(w) for w in phrase.split(" "))
        if re.search(r"\b" + pattern + r"\b", normalized):
            hits.add(phrase)
    return hits


def _prefilter_job(
    job: dict,
    user_skills: set,
    max_age_days: int = JOB_MAX_AGE_DAYS,
    query_phrases: frozenset = frozenset(),
) -> tuple[bool, str]:
    """Apply relevance pre-filter. Returns (pass, reason).

    Freshness (rule 0) runs first — cheapest to check and the most user-
    impactful (a stale posting wastes every downstream cycle).
    """
    title = (job.get("title") or "").lower()
    desc = job.get("description") or ""
    location = (job.get("location") or "").lower()

    # Rule 0: Freshness — apply ASAP from posting is the entire goal
    age = _job_age_days(job)
    if age is not None and age > max_age_days:
        return False, f"stale:{int(age)}d_old"

    # Rule 1: Seniority / off-track-function filter (renamed from
    # "too_senior" — the set now also covers function mismatches like
    # "sales engineer" that have nothing to do with seniority, and an
    # honest reason string matters for anyone reading the reject logs).
    for kw in REJECT_TITLE_KEYWORDS:
        if kw in title:
            return False, f"role_mismatch:{kw}"
    word_match = _WORD_BOUNDARY_REJECT_PATTERN.search(title)
    if word_match:
        return False, f"role_mismatch:{word_match.group(1).lower()}"

    # Rule 2: Description quality gate
    if len(desc) < 200:
        return False, "description_too_short"

    # Rule 3: Minimum skill overlap. Two independent signals, either one
    # sufficient: the closed tech-keyword vocabulary (>=2 matches; unchanged
    # from before, so an IT user's admit/reject behaviour on this path is
    # identical), OR at least one of the user's own query phrases showing up
    # in the JD (domain-neutral: works for "registered nurse" exactly as it
    # does for "site reliability engineer"). Without this second path, any
    # user whose target role isn't in the hardcoded tech vocabulary would
    # have every job rejected here regardless of real relevance. A single
    # PHRASE match (not a lone word -- see _query_phrases) is the bar here,
    # not >=2, because a two-word-or-longer literal phrase match is already
    # a precise, low-noise signal on its own.
    desc_lower = desc.lower()
    jd_skills = _extract_tech_keywords(desc)
    tech_overlap = jd_skills & user_skills
    phrase_overlap = _query_phrase_overlap(desc_lower, query_phrases)
    if len(tech_overlap) < 2 and not phrase_overlap:
        return False, f"skill_overlap:{len(tech_overlap)}"

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
    result = db.table("jobs_raw").select("job_hash, title, company, source, description, location, posted_date") \
        .gte("scraped_at", today).execute()

    all_jobs = result.data or []

    # --- Source 3: Catch unscored jobs from recent days (backfill) ---
    # If the pipeline failed mid-run or was offline, jobs sit in jobs_raw
    # but never make it to the scored 'jobs' table. Pick them up within
    # BACKFILL_LOOKBACK_DAYS (see its own comment above for why 30, not 7,
    # and why widening this doesn't let stale postings through).
    lookback = (datetime.now(timezone.utc).date() - timedelta(days=BACKFILL_LOOKBACK_DAYS)).isoformat()
    recent = db.table("jobs_raw").select("job_hash, title, company, source, description, location, posted_date") \
        .gte("scraped_at", lookback).lt("scraped_at", today).execute()
    if recent.data:
        today_hashes = {j["job_hash"] for j in all_jobs}
        backfill = [j for j in recent.data if j["job_hash"] not in today_hashes]
        if backfill:
            all_jobs.extend(backfill)
            logger.info(f"[merge_dedup] Backfill: {len(backfill)} unscored jobs from last {BACKFILL_LOOKBACK_DAYS} days")

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
    query_phrases: frozenset = frozenset()
    if user_id:
        try:
            config = db.table("user_search_configs").select("queries").eq("user_id", user_id).execute()
            user_queries = config.data[0].get("queries") if config.data else None
            if user_queries:
                # Add user's search queries as additional skill signals, and
                # derive phrase-level signals too (see _query_phrases) --
                # the tech-keyword vocabulary above can't recognise a
                # non-IT user's own terms, so query_phrases is what makes
                # Rule 3 in _prefilter_job work for e.g. "registered nurse"
                # or "staff accountant", not just software roles.
                for q in user_queries:
                    user_skills |= {w.lower() for w in q.split() if len(w) > 2}
                query_phrases = _query_phrases(user_queries)
        except Exception:
            pass

    filtered_jobs = []
    filtered_out = 0
    for job in unique_jobs:
        passes, reason = _prefilter_job(job, user_skills, query_phrases=query_phrases)
        if passes:
            filtered_jobs.append(job)
        else:
            filtered_out += 1
            logger.debug(f"[pre-filter] Rejected: {job.get('title')} — {reason}")

    # --- Check which are truly new (not already scored for this user) ---
    existing_hashes = set()
    existing_dedup_keys: set[str] = set()
    if user_id:
        existing = db.table("jobs").select("job_hash, company, title").eq("user_id", user_id) \
            .not_.is_("job_hash", "null").execute()
        for j in (existing.data or []):
            existing_hashes.add(j["job_hash"])
            # Build cross-query dedup key: same company+title already in jobs table
            norm_co = normalize_company(j.get("company", ""))
            norm_ti = normalize_whitespace(j.get("title", "")).lower()
            existing_dedup_keys.add(f"{norm_co}|{norm_ti}")

    def _new_job_dedup_key(job: dict) -> str:
        return f"{normalize_company(job.get('company', ''))}|{normalize_whitespace(job.get('title', '')).lower()}"

    cross_query_skipped = 0
    batch_dedup_skipped = 0
    new_jobs: list[dict] = []
    batch_dedup_keys: set[str] = set()  # Track within current batch too
    semantic = None
    if os.environ.get("SEMANTIC_DEDUP", "off") == "on":
        from retrieval.dedup import SemanticDedupSession
        semantic = SemanticDedupSession()
    for j in filtered_jobs:
        if j["job_hash"] in existing_hashes:
            continue
        key = _new_job_dedup_key(j)
        if key in existing_dedup_keys:
            cross_query_skipped += 1
            continue
        if key in batch_dedup_keys:
            batch_dedup_skipped += 1
            logger.debug(f"[batch dedup] Skipping duplicate in batch: '{j.get('title')}' @ '{j.get('company')}'")
            continue
        # Tier 4: semantic. Catches the same posting reworded across queries.
        # Guarded: a retrieval failure keeps the job rather than aborting the
        # step that persists the whole day's scrape. See SemanticDedupSession.
        if semantic is not None and semantic.find_duplicate(j):
            filtered_out += 1
            continue
        batch_dedup_keys.add(key)
        new_jobs.append(j)

    if batch_dedup_skipped:
        logger.info(f"[merge_dedup] Batch dedup: skipped {batch_dedup_skipped} within-batch duplicates")

    if cross_query_skipped:
        logger.info(f"[merge_dedup] Cross-query dedup: skipped {cross_query_skipped} jobs already in jobs table")

    # --- Hard capacity cap (see MAX_JOBS_PER_RUN comment) ---
    # Deliberately kept separate from `filtered_out`: that field means "did
    # not pass the relevance/quality rules", this is "passed everything but
    # there wasn't scoring capacity for it today" — a different reason a
    # future reader (or self_improver.py) shouldn't have to disentangle from
    # the logs after the fact.
    capacity_capped = 0
    if len(new_jobs) > MAX_JOBS_PER_RUN:
        capacity_capped = len(new_jobs) - MAX_JOBS_PER_RUN
        new_jobs.sort(key=lambda j: _job_relevance_rank(j, user_skills), reverse=True)
        new_jobs = new_jobs[:MAX_JOBS_PER_RUN]
        logger.info(
            f"[merge_dedup] Capacity cap: {len(new_jobs) + capacity_capped} passed the relevance filter, "
            f"MAX_JOBS_PER_RUN={MAX_JOBS_PER_RUN} — dropped the {capacity_capped} lowest skill-overlap matches"
        )

    new_hashes = [j["job_hash"] for j in new_jobs]

    logger.info(
        f"[merge_dedup] {len(all_jobs)} scraped → {len(unique_jobs)} unique "
        f"→ {len(filtered_jobs)} passed filter ({filtered_out} filtered) "
        f"→ {len(new_hashes)} new for scoring"
    )
    return {
        "new_job_hashes": new_hashes,
        "total_new": len(new_hashes),
        "filtered_out": filtered_out,
        "capacity_capped": capacity_capped,
        # Present only when SEMANTIC_DEDUP is on. Lets a reader tell "no
        # duplicates found" apart from "dedup was broken for this run".
        **({"semantic_dedup": semantic.stats()} if semantic is not None else {}),
    }
