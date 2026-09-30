import json
import logging
import random
import re
import statistics
import uuid
from datetime import datetime


# Import resolution mirrors lambdas/pipeline/retrieval/embeddings.py. This
# module lives inside the pipeline Lambdas' CodeUri (template.yaml:
# CodeUri: lambdas/pipeline/), which SAM flattens into /var/task, making
# `ai_helper` a flat sibling there — the same shape tests/conftest.py creates.
# The container-image Lambda (Dockerfile.lambda) instead ships the whole
# lambdas/ tree, where only the qualified import resolves. Try flat first since
# it covers both pytest and the zip Lambda; fall back for the container shape.
#
# This was not merely tidiness: app.py (the API container) now imports this
# module, and with the bare import alone it raised
# "ModuleNotFoundError: No module named 'ai_helper'" at container start —
# invisible locally, because conftest puts lambdas/pipeline on sys.path.
try:
    from ai_helper import ai_complete_cached, get_supabase
except ImportError:  # container-image shape only
    from lambdas.pipeline.ai_helper import ai_complete_cached, get_supabase

# Same two shapes, one extra wrinkle: guardrails/input_guards.py imports its
# own siblings unqualified (`from guardrails.policy import policy_for`), so the
# qualified spelling alone is NOT enough in the container — importing
# lambdas.pipeline.guardrails.input_guards there raises ModuleNotFoundError
# from inside that module. Registering the package under its flat name first
# makes the sibling import resolve without putting lambdas/pipeline on
# sys.path, which would shadow the repo-root `utils/` package that app.py
# imports (lambdas/pipeline/utils/ mirrors it filename-for-filename). Same
# trick, same reason, as mcp_server/server.py's ai_helper shim.
try:
    from guardrails.input_guards import (  # flat — pytest and the zip Lambdas
        INSTRUCTION_HIERARCHY,
        check_input,
        fence,
        maybe_scrub_pii,
    )
except ImportError:  # container-image shape only
    import sys as _sys

    from lambdas.pipeline import guardrails as _guardrails_pkg

    _sys.modules.setdefault("guardrails", _guardrails_pkg)
    from guardrails.input_guards import (
        INSTRUCTION_HIERARCHY,
        check_input,
        fence,
        maybe_scrub_pii,
    )
from shared.apply_platform import classify_apply_platform, extract_platform_ids
from shared.work_auth import apply_geo_score_cap
from shared.tex_utils import tex_to_plaintext

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def score_to_tier(score: float | None) -> str:
    """Map match_score (0-100) to tier letter (S/A/B/C/D).

    Thresholds from unified grand plan (Phase 2.10):
      S 90+, A 80-89, B 70-79, C 60-69, D <60.
    """
    if score is None:
        return "D"
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


# Tech-domain keywords — job titles matching any of these pass the prefilter.
# Case-insensitive substring match. Covers software, infra, data, ML/AI, security.
_TECH_TITLE_KEYWORDS = (
    "software", "engineer", "developer", "programmer", "architect",
    "sre", "devops", "platform", "infrastructure", "infra", "cloud",
    "backend", "frontend", "fullstack", "full-stack", "full stack",
    "data", "ml ", "mle", "ai/", " ai ", "machine learning", "llm",
    "security", "cyber", "site reliability", "systems",
    "python", "javascript", "typescript", "react", "node",
    "kubernetes", "aws", "gcp", "azure", "linux",
    "staff ", "principal ", "senior ", "junior ", "graduate",
    "tech lead", "technical lead", "eng", "qa ",
)

# Hard reject titles — obvious non-tech roles we should never score.
_NON_TECH_TITLE_REJECTS = (
    "nurse", "doctor", "physician", "pharmacist", "dentist", "therapist",
    "teacher", "tutor", "professor", "lecturer", "instructor",
    "sales executive", "sales manager", "sales director", "account executive",
    "account manager", "customer success", "customer service", "call center",
    "marketing manager", "marketing director", "brand manager", "copywriter",
    "hr ", "human resources", "recruiter", "talent acquisition",
    "accountant", "bookkeeper", "auditor", "tax ", "finance manager",
    "lawyer", "attorney", "paralegal", "legal counsel",
    "driver", "delivery", "warehouse", "retail", "cashier", "barista",
    "cleaner", "janitor", "security guard", "chef", "cook", "waiter",
    "receptionist", "secretary", "administrator", "office manager",
    "social worker", "counsellor", "counselor",
    "construction", "plumber", "electrician", "carpenter", "mechanic",
    "graphic designer", "art director", "content creator",
    "project manager", "product manager", "program manager",
    "business analyst", "business development",
)


def _missing_column_name(exc: Exception) -> str | None:
    """The column name a missing-column error refers to, if it names one.

    PostgREST: "Could not find the 'trace_id' column of 'jobs' ..."
    Postgres:  'column "trace_id" does not exist'
               'column "trace_id" of relation "jobs" does not exist'

    Postgres uses both spellings depending on whether the statement gave it a
    relation to name, and matching only the shorter one leaves the retry unable
    to identify the column in the longer case -- caught by
    test_handler_survives_missing_trace_id_column.
    """
    text = str(exc)
    m = re.search(r"Could not find the '([^']+)' column", text)
    if m:
        return m.group(1)
    m = re.search(r'column "([^"]+)"(?: of relation "[^"]+")? does not exist', text)
    return m.group(1) if m else None


def _is_missing_column_error(exc: Exception) -> bool:
    """True when an insert failed because a column isn't in the schema yet.

    Two different wordings reach us for the same condition, and matching only
    one of them is how the 2026-09-28 run lost all 58 of its scored jobs:

      - PostgREST (what Supabase actually returns): code PGRST204,
        "Could not find the 'trace_id' column of 'jobs' in the schema cache"
      - raw Postgres: 'column "trace_id" does not exist'

    The original guard tested for "does not exist", which the PostgREST
    wording does not contain, so the retry-without-optional-columns path
    never ran and every row was warned-and-dropped instead.
    """
    text = str(exc)
    return (
        "PGRST204" in text
        or ("column" in text and "does not exist" in text)
        or ("Could not find" in text and "column" in text)
    )


def should_skip_scoring(job: dict) -> str | None:
    """Check if job should be skipped for scoring. Returns score_status or None."""
    desc = job.get("description", "") or ""
    if len(desc) < 100:
        return "insufficient_data"
    company = job.get("company", "") or ""
    if not company.strip():
        return "incomplete"
    title = (job.get("title") or "").lower()
    if not title:
        return "incomplete"
    # Hard reject obvious non-tech titles
    if any(bad in title for bad in _NON_TECH_TITLE_REJECTS):
        return "non_tech_role"
    # Keyword prefilter — at least one tech keyword must be present in the title
    if not any(kw in title for kw in _TECH_TITLE_KEYWORDS):
        return "no_tech_keywords"
    return None


def assign_model_for_ab_test(available_providers: list[str], ab_ratio: float = 0.2) -> str:
    """Assign a model for A/B testing. 80% primary, 20% alternate."""
    if len(available_providers) < 2:
        return available_providers[0] if available_providers else None
    if random.random() < ab_ratio:
        return available_providers[1]  # Alternate
    return available_providers[0]  # Primary


def handler(event, context):
    user_id = event["user_id"]
    job_hashes = event.get("new_job_hashes", [])
    min_score = event.get("min_match_score", 60)

    if not job_hashes:
        return {"matched_items": [], "matched_count": 0}

    db = get_supabase()

    # Bulk fetch all jobs in one query
    jobs_result = db.table("jobs_raw").select("*").in_("job_hash", job_hashes).execute()
    jobs = jobs_result.data or []

    # Get latest resume (no is_active column; use most recently created)
    resume_result = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    if not resume_result.data:
        logger.warning(f"[score_batch] No resume found for user {user_id}")
        return {"matched_items": [], "matched_count": 0, "error": "no_resume"}

    resume_row = resume_result.data[0]
    resume_tex = resume_row.get("tex_content", "")
    # user_resumes has no resume_type column — it is resume_key. The wrong name
    # returned "" through .get()'s default rather than raising, so every job the
    # current pipeline scored recorded matched_resume as empty and the
    # dashboard's RESUME TYPE column read "--" for all of them. Rows older than
    # the multi-resume table still hold sre_devops/fullstack from the legacy
    # config path, which made it look abandoned rather than broken.
    resume_type = resume_row.get("resume_key", "")
    if not resume_tex:
        logger.warning(f"[score_batch] Resume tex_content is empty for user {user_id}")
        return {"matched_items": [], "matched_count": 0, "error": "no_resume"}

    # Load user profile for geo / work-auth cap (cheap, single row)
    try:
        user_row = (
            db.table("users")
            .select("work_authorizations,location")
            .eq("id", user_id)
            .single()
            .execute()
            .data
        ) or {}
    except Exception as e:
        logger.warning(f"[score_batch] Could not load user row for cap: {e}")
        user_row = {}
    user_work_auth = user_row.get("work_authorizations") or {}

    matched_items = []
    skipped_count = 0
    inserted = 0
    insert_failures = 0
    for job in jobs:
        skip_status = should_skip_scoring(job)
        if skip_status:
            logger.info(f"[score_batch] Skipping {job['job_hash']}: {skip_status}")
            skipped_count += 1
            continue

        score_result = score_single_job_deterministic(job, resume_tex)

        if score_result is None:
            continue

        # Geo + work-auth aware cap. Caps non-IE jobs at A-tier max (89);
        # caps IE-only candidates' US/UK jobs without sponsor signal at
        # B-tier max (70). See shared/work_auth.py for full rules.
        score_result = apply_geo_score_cap(score_result, job, user_work_auth)

        match_score = score_result.get("match_score", 0)
        if match_score < min_score:
            continue

        url = job.get("apply_url") or ""
        ids = extract_platform_ids(url)
        # Single source of truth: prefer extract_platform_ids' platform when slugs found,
        # fall back to classify_apply_platform for platforms without slug support
        # (lever, workday, etc.) so apply_platform remains informative for them.
        platform_name = ids["platform"] if ids else classify_apply_platform(url)
        job_record = {
            "job_id": str(uuid.uuid4()),
            "user_id": user_id,
            "job_hash": job["job_hash"],
            "title": job["title"],
            "company": job["company"],
            "description": job.get("description"),
            "location": job.get("location"),
            "apply_url": job.get("apply_url"),
            "apply_platform": platform_name,
            "apply_board_token": ids["board_token"] if ids else None,
            "apply_posting_id": ids["posting_id"] if ids else None,
            "source": job["source"],
            "match_score": match_score,
            "score_tier": score_to_tier(match_score),
            "ats_score": score_result.get("ats_score", 0),
            "hiring_manager_score": score_result.get("hiring_manager_score", 0),
            "tech_recruiter_score": score_result.get("tech_recruiter_score", 0),
            "key_matches": score_result.get("key_matches", []),
            "gaps": score_result.get("gaps", []),
            "match_reasoning": score_result.get("reasoning", ""),
            "archetype": score_result.get("archetype", ""),
            "seniority": score_result.get("seniority", ""),
            "remote": score_result.get("remote", ""),
            "requirement_map": score_result.get("requirement_map", []),
            "tailoring_model": f"{score_result.get('provider', 'council')}:{score_result.get('model', 'consensus')}",
            "matched_resume": resume_type,
            "first_seen": datetime.utcnow().isoformat(),
            # Links this row to the LangGraph council run that produced its
            # score, when the AI call that scored it went through the
            # council and returned one (Task 10). None today for every row:
            # score_single_job (above) calls ai_complete_cached, a single-
            # model call that never returns a trace_id -- only
            # council_complete_langgraph does. Forward-compatible plumbing,
            # not a claim that scoring currently runs through the council.
            "trace_id": score_result.get("trace_id"),
        }
        try:
            db.table("jobs").insert(job_record).execute()
            inserted += 1
        except Exception as e:
            # Retry without the column the error actually names.
            #
            # This used to pop a hardcoded list of 13 "optional" columns
            # whenever ANY one of them was missing. Measured on 2026-09-28:
            # trace_id alone being absent cost score_tier, key_matches, gaps,
            # match_reasoning, archetype, seniority, remote, requirement_map
            # and matched_resume on every row -- 17 jobs landed with a score
            # and no tier, which breaks the dashboard's tier filter. One
            # missing column should cost one column.
            if _is_missing_column_error(e):
                dropped, err = [], e
                while _is_missing_column_error(err):
                    col = _missing_column_name(err)
                    if not col or col not in job_record:
                        break          # can't identify it -- stop, don't guess
                    job_record.pop(col)
                    dropped.append(col)
                    try:
                        db.table("jobs").insert(job_record).execute()
                        err = None
                        break
                    except Exception as retry_err:
                        err = retry_err
                if err is not None:
                    insert_failures += 1
                    logger.warning(
                        f"[score_batch] Insert retry failed for {job['job_hash']} "
                        f"after dropping {dropped or 'nothing'}: {err}")
                    continue
                logger.warning(
                    f"[score_batch] Inserted {job['job_hash']} without {dropped} "
                    f"-- apply the migration that adds these columns")
                inserted += 1
            else:
                insert_failures += 1
                logger.warning(f"[score_batch] Insert failed for {job['job_hash']}: {e}")
                continue

        if match_score >= 85:
            tailoring_depth = "light"
        elif match_score >= 70:
            tailoring_depth = "moderate"
        else:
            tailoring_depth = "heavy"

        # Artifact rules: resume for B+, cover letter for A+, contacts for S+A
        tier = score_to_tier(match_score)
        matched_items.append({
            "job_hash": job["job_hash"],
            "user_id": user_id,
            "tailoring_depth": tailoring_depth,
            "light_touch": tailoring_depth == "light",
            "skip_cover_letter": tier in ("B", "C"),
            "skip_contacts": tier in ("B", "C"),
        })

    logger.info(
        f"[score_batch] {len(jobs)} fetched, {skipped_count} skipped, "
        f"{inserted} inserted, {insert_failures} insert-failed, "
        f"{len(matched_items)} matched (min_score={min_score})"
    )
    # A per-row insert failure is tolerable; every row failing is not. That
    # shape means something systemic -- an unapplied migration, an RLS change,
    # bad credentials -- and swallowing it is how a run scores 58 jobs, writes
    # none of them, and still reports SUCCEEDED to Step Functions.
    if insert_failures and inserted == 0:
        raise RuntimeError(
            f"[score_batch] all {insert_failures} inserts into jobs failed; "
            "refusing to report success. Check the most recent [score_batch] "
            "Insert failed warning for the underlying database error."
        )
    return {
        "matched_items": matched_items,
        "matched_count": len(matched_items),
        "skipped_count": skipped_count,
        "inserted": inserted,
        "insert_failures": insert_failures,
    }


SCORE_SYSTEM_PROMPT = """You are an expert job-candidate evaluator. Score how well a candidate's resume matches a job listing from THREE distinct perspectives.

SCORING PERSPECTIVES (each 0-100):

1. **ATS Score** — Automated screening lens. Focus on: exact keyword matches between resume and JD, job title alignment, required certifications present, section structure (experience, skills, education), formatting compatibility with ATS parsers.

2. **Hiring Manager Score** — Business leader lens. Focus on: demonstrated impact with metrics and outcomes, relevance of past projects to the role, career trajectory and growth narrative, leadership signals, cultural alignment indicators, communication clarity.

3. **Technical Recruiter Score** — Technical screening lens. Focus on: coverage of required vs preferred tech stack, depth of experience with core technologies, seniority-level alignment (years + complexity of past work), red flags (job hopping, unexplained gaps, technology mismatches).

CALIBRATION GUIDE — use the full 0-100 range:
- 90-100: Exceptional match. Candidate could be shortlisted immediately with zero resume changes. All required skills present, strong experience alignment.
- 80-89: Strong match. Minor gaps that tailoring could address. Most required skills present.
- 70-79: Good match. Some relevant experience but notable gaps. Worth tailoring.
- 60-69: Moderate match. Partial skill overlap, significant gaps. Tailoring may help.
- 50-59: Weak match. Limited relevance. Only worth pursuing if few better options.
- 0-49: Poor match. Fundamental misalignment in skills, experience, or seniority.

IMPORTANT: Use the full range. A score of 75 is meaningfully different from 85.
Do NOT cluster all scores in the 70-85 range — differentiate clearly.

ANTI-INFLATION RULES:
- If the resume lacks a REQUIRED skill explicitly stated in the JD, ATS score cannot exceed 75.
- If the resume has no metrics or quantified achievements relevant to the role, HM score cannot exceed 70.
- If fewer than 3 of the top 5 required technologies listed in the JD are present in the resume, TR score cannot exceed 75.

SCORING GUIDANCE FOR JUNIOR/GRADUATE ROLES:
- For roles marked as "Junior", "Graduate", "Entry Level", or "Associate": be MORE lenient with experience requirements.
- A strong portfolio and relevant coursework/projects can compensate for fewer years of experience.
- Do NOT penalize junior roles for listing technologies the candidate hasn't used.
- Anti-inflation rules still apply but with relaxed thresholds: ATS cap becomes 80, TR cap becomes 80.

CRITICAL — INTERN/STUDENT ELIGIBILITY FILTER:
- Check the candidate's resume/education section for whether they are a CURRENTLY ENROLLED student (an in-progress degree, no completion date) versus already graduated/not a student.
- If the JD explicitly requires "currently enrolled", "must be pursuing a degree", "ongoing education", "returning to studies after internship", or similar language indicating the role is ONLY for current students, AND the candidate's resume does not show them as a currently enrolled student: ALL scores must be capped at 40 (effectively disqualifying the role).
- This applies to most internship programs. Roles like "New Grad" or "Entry Level" that do NOT require current enrollment are fine regardless of the candidate's student status.
- Add "ineligible: not currently enrolled student" to the gaps list when this filter triggers.

STRUCTURED EVALUATION (career-ops Block A+B methodology):
Before scoring, classify the role and map requirements to resume evidence:

Block A — Role Classification:
- Archetype: one of [SRE/DevOps, Backend, Full-Stack, Platform/Cloud, Data, Frontend, AI/ML]
- Seniority: one of [Junior/Graduate, Mid-Level, Senior, Staff/Lead]
- Remote: one of [Remote, Hybrid, On-site, Unknown]

Block B — Requirement Mapping:
For each KEY requirement in the JD, cite the SPECIFIC resume evidence that satisfies it.
If no evidence exists, mark as a gap with severity (blocker vs nice-to-have).

OUTPUT LENGTH — a hard constraint, not a preference:
- "requirement_map": at most 8 entries, blocker_gap first. Do NOT list every
  requirement; list the 8 that most affect the decision.
- Keep each "requirement" and "evidence" string under 120 characters.
- "key_matches" and "gaps": at most 8 items each.
- "reasoning": 2-3 sentences, no more.
A response that overruns the token budget is truncated and its trailing fields
are lost, so brevity here is correctness, not style.

Return ONLY valid JSON (no markdown, no code fences):
{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "match_score": <0-100 weighted average>,
    "archetype": "<role archetype>",
    "seniority": "<detected seniority level>",
    "remote": "<remote status>",
    "reasoning": "<2-3 sentences explaining the scores and key factors>",
    "key_matches": ["<skill1>", "<skill2>", ...],
    "gaps": ["<missing_skill1>", "<missing_experience1>", ...],
    "requirement_map": [
        {"requirement": "<JD requirement>", "evidence": "<resume evidence or null>", "severity": "<met|nice_to_have_gap|blocker_gap>"},
        ...
    ]
}"""


# --- Groq free-tier token budget -------------------------------------------
# Measured 2026-09-01 against the production key: x-ratelimit-limit-tokens=8000
# tokens per MINUTE, and Groq bills prompt_tokens + max_tokens against it — so
# an unused max_tokens still burns quota.
#
# The old call sent the resume as raw LaTeX and inherited ai_complete_cached's
# max_tokens=4096 default:  3792 + 4096 = 7888, right on the limit, which
# returned 413 "Request too large" for most jobs and scored nothing.
#
# Cap the job description (the tail is benefits/EEO boilerplate, not signal)
# and pass an explicit max_tokens. 1024 was measured to truncate the response
# mid-JSON (finish_reason='length') — that produced the "[score_batch] JSON
# parse error" log lines. 1536 completed cleanly using 1206 completion tokens,
# 702 of them reasoning; 2048 leaves headroom for more verbose jobs.
MAX_DESCRIPTION_CHARS = 4000
SCORE_MAX_TOKENS = 2048


# The three perspective scores are declared FIRST in the schema above, so a
# response cut off in the trailing prose still carries all of them.
_SALVAGE_PERSPECTIVES = ("ats_score", "hiring_manager_score", "tech_recruiter_score")


def _salvage_scores(text: str) -> dict | None:
    """Recover the numeric scores from a JSON object truncated mid-string.

    Measured in CI on 2026-09-28: 10 of 25 eval cases failed as JSON parse
    errors, not provider failures. A model answered every time; the answer was
    cut off inside `requirement_map` or `reasoning`, and the whole job was
    dropped — including three scores that had already arrived intact.

    This recovers ONLY the numbers, and only when all three perspectives are
    present and in range. Everything else (reasoning, key_matches, gaps,
    requirement_map) is genuinely lost and is left to the caller's defaults.
    A perspective score outside 0-100 is treated as corruption rather than
    clamped: the point is to rescue work the model did, never to invent it.
    """
    out: dict = {}
    for field in (*_SALVAGE_PERSPECTIVES, "match_score"):
        m = re.search(rf'"{field}"\s*:\s*(-?\d+)', text)
        if not m:
            continue
        value = int(m.group(1))
        if 0 <= value <= 100:
            out[field] = value
        elif field in _SALVAGE_PERSPECTIVES:
            return None
    if not all(f in out for f in _SALVAGE_PERSPECTIVES):
        return None
    return out


class UntrustedInputRejected(ValueError):
    """A guarded scoring call was handed text the input guards blocked.

    Raised, not returned as None: `score_single_job` returns None when the
    model call or the JSON parse failed, and a caller cannot tell those apart
    from "we refused to send this". An MCP tool call surfaces this to the
    client as a tool error, which is the honest answer. Subclasses ValueError
    so existing `except ValueError` handlers keep working.
    """


def _guard_untrusted_scoring_input(description: str, resume_text: str) -> tuple[str, str]:
    """Run the three input-guard layers over client-supplied scoring text.

    1. detect  — `check_input` against the "score" policy's injection patterns
    2. scrub   — `maybe_scrub_pii`, which that same policy enables
    3. fence   — delimiters the text cannot forge its way out of

    The caller supplies the fourth layer by appending INSTRUCTION_HIERARCHY to
    the system prompt; a fence with nothing declaring what it means is
    decorative.
    """
    for label, text in (("job description", description), ("resume", resume_text)):
        result = check_input(text, "score")
        if not result.passed:
            blocked = "; ".join(
                f"{v.rule}: {v.detail}" for v in result.violations if v.severity == "block"
            )
            raise UntrustedInputRejected(f"{label} rejected by input guards ({blocked})")

    description = maybe_scrub_pii(description, "score")
    resume_text = maybe_scrub_pii(resume_text, "score")
    return fence(description, "JOB_DESCRIPTION"), fence(resume_text, "RESUME")


def score_single_job(job: dict, resume_tex: str, temperature: float = 0,
                     skip_cache: bool = False, untrusted_input: bool = False) -> dict | None:
    """Score a single job against the user's resume using 3-perspective AI scoring.
    Uses the same prompt template as matcher.py for consistency.

    Parameters
    ----------
    temperature:
        Sampling temperature for the AI call. Default 0 for deterministic scoring.
    untrusted_input:
        When True, the description and resume are treated as third-party text:
        injection-checked (raising `UntrustedInputRejected` rather than
        scoring), PII-scrubbed, fenced, and accompanied by the instruction
        hierarchy in the system prompt. Default False, which produces a
        byte-identical prompt and system string to before this parameter
        existed — deliberately, because `ai_complete_cached` keys its cache on
        both, so guarding unconditionally would invalidate every scoring cache
        entry and force a full re-score of the backlog (1,280 rows as of
        2026-09-30). The pipeline's scraped descriptions are untrusted too and
        should opt in once that cost is budgeted; `mcp_server.score_job`, whose
        text comes from whatever client connects, opts in today.
    """
    location = job.get("location") or "Not specified"
    remote_value = job.get("remote")
    remote_str = "Not specified" if remote_value in (None, "") else str(remote_value)

    # Plaintext, not LaTeX: markup carries no scoring signal and costs ~22% of
    # the resume's tokens (12,500 -> 9,705 chars on the current base resume).
    resume_text = tex_to_plaintext(resume_tex)
    description = (job.get("description") or "")[:MAX_DESCRIPTION_CHARS]

    system = SCORE_SYSTEM_PROMPT
    if untrusted_input:
        # Before the f-string below, and before the try: a rejection must
        # propagate, not be swallowed by the model-failure handler.
        description, resume_text = _guard_untrusted_scoring_input(description, resume_text)
        system = f"{SCORE_SYSTEM_PROMPT}\n\n{INSTRUCTION_HIERARCHY}"

    prompt = f"""Score this job against the candidate's resume.

Job: {job['title']} at {job['company']}
Location: {location}
Remote: {remote_str}
Description: {description}

Resume: {resume_text}"""

    try:
        response_dict = ai_complete_cached(
            prompt, system=system, temperature=temperature,
            max_tokens=SCORE_MAX_TOKENS, skip_cache=skip_cache,
        )
        text = response_dict["content"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError as parse_error:
            result = _salvage_scores(text)
            if result is None:
                logger.error(
                    f"[score_batch] JSON parse error for {job['job_hash']}: "
                    f"{parse_error} (no scores recoverable, {len(text)} chars)")
                return None
            # Flagged, never silent: a salvaged score must be distinguishable
            # from a clean parse by anything that reads this.
            result["truncated"] = True
            logger.warning(
                f"[score_batch] truncated response for {job['job_hash']} "
                f"({parse_error}) — salvaged the scores, lost the prose fields")

        # Include model info so we can save it to DB
        result["provider"] = response_dict.get("provider", "council")
        result["model"] = response_dict.get("model", "auto")
        # Carries a council trace_id through if the underlying call ever
        # produces one (today ai_complete_cached/ai_complete never do -- see
        # handler()'s job_record comment); `.get` keeps this a no-op absent
        # that, rather than requiring every caller of ai_complete_cached to
        # grow the key first.
        result["trace_id"] = response_dict.get("trace_id")

        # Ensure match_score is computed consistently
        if "match_score" not in result:
            ats = result.get("ats_score", 0)
            hm = result.get("hiring_manager_score", 0)
            tr = result.get("tech_recruiter_score", 0)
            result["match_score"] = round((ats + hm + tr) / 3)

        return result
    except Exception as e:
        # Parse failures are handled above, where the raw text is still in
        # scope and salvage is possible; this is for the call itself failing.
        logger.error(f"[score_batch] AI scoring failed for {job['job_hash']}: {e}")
        return None


def score_single_job_deterministic(
    job: dict, resume_tex: str, num_calls: int = 1, skip_cache: bool = False,
    untrusted_input: bool = False,
) -> dict | None:
    """Score a job `num_calls` times at temperature=0; return medians and the spread.

    The median dampens provider variance. The SPREAD is what makes the result
    honest: measured 2026-09-28, one model given one identical prompt at
    temperature=0 returned three different answers to three consecutive calls,
    because temperature controls sampling and not mixture-of-experts routing or
    request batching. Reporting a single integer claims a precision the
    measurement does not have, so callers can render a band instead.

    skip_cache must be True for num_calls > 1 to mean anything — otherwise every
    call after the first returns the same cached response and the median of
    three is the median of one. The default (one call, cache on) is what the
    batch pipeline uses and is deliberately unchanged: it scores ~58 jobs a run
    against an 8k tokens/minute Groq ceiling that is already the bottleneck.

    The returned dict keeps score_single_job's shape and adds::

        score_spread = {"ats": [lo, hi], "hiring_manager": [lo, hi],
                        "tech_recruiter": [lo, hi], "match": [lo, hi],
                        "n": <calls that actually succeeded>}

    `n` is reported so a caller can tell one sample from three agreeing ones.

    Returns None only when every call fails.
    """
    all_scores: list[dict] = []
    for _ in range(num_calls):
        result = score_single_job(
            job, resume_tex, temperature=0, skip_cache=skip_cache,
            untrusted_input=untrusted_input,
        )
        if result is not None:
            all_scores.append(result)

    if not all_scores:
        return None

    _FIELDS = {
        "ats": "ats_score",
        "hiring_manager": "hiring_manager_score",
        "tech_recruiter": "tech_recruiter_score",
        "match": "match_score",
    }

    def _vals(key):
        return [s[key] for s in all_scores if isinstance(s.get(key), (int, float))]

    spread: dict = {"n": len(all_scores)}
    for short, key in _FIELDS.items():
        vals = _vals(key)
        spread[short] = [min(vals), max(vals)] if vals else None

    # Non-numeric fields (reasoning, key_matches, gaps, provider, model) come
    # from the first result, so the dict keeps score_single_job's shape and
    # existing callers keep working.
    merged = dict(all_scores[0])
    for short, key in _FIELDS.items():
        vals = _vals(key)
        if not vals:
            continue
        merged[key] = (round(statistics.median(vals), 1) if key == "match_score"
                       else int(statistics.median(vals)))
    merged["score_spread"] = spread
    return merged


def compute_base_scores(job: dict, base_resume: str) -> dict:
    """Score base (untailored) resume against JD. Returns base_* scores."""
    scores = score_single_job_deterministic(job, base_resume)
    if not scores:
        return {}
    return {
        "base_ats_score": scores["ats_score"],
        "base_hm_score": scores["hiring_manager_score"],
        "base_tr_score": scores["tech_recruiter_score"],
        "match_score": scores["match_score"],
    }


def compute_tailored_scores(job: dict, tailored_resume: str) -> dict:
    """Score tailored resume against JD. Returns tailored_* scores."""
    scores = score_single_job_deterministic(job, tailored_resume)
    if not scores:
        return {}
    return {
        "tailored_ats_score": scores["ats_score"],
        "tailored_hm_score": scores["hiring_manager_score"],
        "tailored_tr_score": scores["tech_recruiter_score"],
        "final_score": scores["match_score"],
    }


WRITING_QUALITY_PROMPT = """Rate this resume on a scale of 1-10 for each dimension:
- specificity: Does it use specific numbers, technologies, and outcomes instead of vague claims?
- impact_language: Does it use strong action verbs and quantify achievements?
- authenticity: Does it sound like a real person wrote it, free of AI filler and buzzwords?
- readability: Is it clear, concise, and well-structured?

Return JSON only: {"specificity": N, "impact_language": N, "authenticity": N, "readability": N}"""


def score_writing_quality(resume_text: str) -> dict:
    """Score resume writing quality using AI. Returns quality dimensions + average."""
    import json as _json
    result = ai_complete_cached(
        prompt=resume_text,
        system=WRITING_QUALITY_PROMPT,
        temperature=0,
    )
    try:
        content = result.get("content", "") if isinstance(result, dict) else result
        # Handle markdown code fences
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        scores = _json.loads(content)
        avg = sum(scores.values()) / len(scores)
        scores["writing_quality_score"] = round(avg, 1)
        return scores
    except (json.JSONDecodeError, KeyError, ZeroDivisionError, TypeError, ValueError):
        return {"writing_quality_score": None}
