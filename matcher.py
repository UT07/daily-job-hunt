"""Job-resume matching engine using 3-perspective scoring.

Scores each job against the candidate's BASE resume from three viewpoints:
1. ATS (Applicant Tracking System) — keyword match, formatting, structure
2. Hiring Manager — impact, relevance, culture fit, growth potential
3. Technical Recruiter — required/preferred skills coverage, experience level

Jobs are scored in batches of 5 to reduce API calls and token usage.
A job passes matching if the average of all 3 scores meets the threshold.
"""

from __future__ import annotations
import json
import logging
import re
from typing import List, Dict, Optional, TYPE_CHECKING
from scrapers.base import Job
from ai_client import AIClient
from quality_logger import log_quality

if TYPE_CHECKING:
    from user_profile import UserProfile

logger = logging.getLogger(__name__)


# ── Default (hardcoded) candidate context — used when no UserProfile is provided ──

_DEFAULT_CANDIDATE_CONTEXT = """\
- Fresh MSc Cloud Computing graduate with 2+ years of industry experience
- Indian citizen residing in Dublin, Ireland
- Work authorization:
  * Ireland: Stamp 1G — eligible for full-time employment
  * India: Indian citizen — eligible for full-time employment (remote only, based in Ireland)
  * US: Requires visa sponsorship (H-1B/L-1) — only apply if company sponsors
- Targeting both fresh-grad/entry-level AND mid-level roles
- Has two resume variants: sre_devops (SRE/DevOps/Platform) and fullstack (Full-Stack/Backend)"""

_DEFAULT_CANDIDATE_CONTEXT_SHORT = """\
- Fresh MSc Cloud Computing graduate with 2+ years of industry experience
- Indian citizen residing in Dublin, Ireland
- Ireland: Stamp 1G (full-time eligible). India: remote only. US: needs sponsorship.
- Two resume variants: sre_devops and fullstack
- India roles must be remote and pay ≥₹10 LPA. US roles must sponsor visas."""

_DEFAULT_CANDIDATE_INFO = """\
- Location: Dublin, Ireland
- Visa: Stamp 1G (eligible for full-time work in Ireland)
- Citizenship: Indian (would need sponsorship for roles outside Ireland)
- Target: Fresh grad roles (where experience gives an edge) and mid-level roles"""

_DEFAULT_CANDIDATE_INFO_SHORT = """\
- Location: Dublin, Ireland
- Visa: Stamp 1G (eligible for full-time work in Ireland)
- Citizenship: Indian (would need sponsorship for roles outside Ireland)
- Target: Fresh grad roles and mid-level roles"""


def _build_match_system_prompt(candidate_context: str) -> str:
    """Build the batch-matching system prompt with the given candidate context."""
    return f"""You are an expert job-candidate evaluator. Score how well a candidate's resume matches MULTIPLE job listings from THREE distinct perspectives.

SCORING PERSPECTIVES (each 0-100):

1. **ATS Score** — Applicant Tracking System:
   - Keyword match: Does the resume contain the key terms from the job description?
   - Section structure: Standard sections present (Experience, Education, Skills)?
   - Job title alignment: Does the candidate's background map to this role?
   - Hard requirements: Years of experience, degree level, certifications

2. **Hiring Manager Score** — The person who manages this hire:
   - Relevant impact: Do accomplishments relate to what this team needs?
   - Experience narrative: Does the career story make sense for this position?
   - Culture/team fit signals: Would this person thrive in this environment?
   - Growth potential: For junior roles, does the candidate show strong learning ability?

3. **Technical Recruiter Score** — First-pass technical screening:
   - Required skills coverage: What % of "must have" skills are demonstrated?
   - Preferred skills coverage: What % of "nice to have" skills are present?
   - Experience level match: Does YoE and project complexity match?
   - Red flags: Gaps, mismatches, overqualified/underqualified signals?

IMPORTANT candidate context:
{candidate_context}

GEOGRAPHIC SCORING RULES:
- Ireland-based roles: No visa penalty. Score normally.
- India-based roles: Must be REMOTE. If in-office in India, score 0 (candidate lives in Ireland).
- US/other roles: Must be REMOTE + company must sponsor visas. Penalize score by 10 if sponsorship unclear.
- Salary: India roles below ₹10 LPA (~$12k USD) should score 0.

Be honest and strict. Don't inflate scores — this determines which jobs are worth applying to.

Return ONLY a valid JSON array (no markdown, no code fences). One object per job, in the same order as presented:
[
    {{
        "job_index": 0,
        "ats_score": <0-100>,
        "hiring_manager_score": <0-100>,
        "tech_recruiter_score": <0-100>,
        "best_resume": "<sre_devops or fullstack>",
        "reasoning": "<2-3 sentences>",
        "key_matches": ["<skill1>", ...],
        "gaps": ["<gap1>", ...],
        "tailoring_suggestions": ["<suggestion1>", ...]
    }},
    ...
]"""


def _build_single_match_system_prompt(candidate_context_short: str) -> str:
    """Build the single-job matching system prompt with the given candidate context."""
    return f"""You are an expert job-candidate evaluator. Score how well a candidate's resume matches a job listing from THREE distinct perspectives.

SCORING PERSPECTIVES (each 0-100):

1. **ATS Score** — keyword match, formatting, section structure, title alignment
2. **Hiring Manager Score** — relevant impact, experience narrative, culture fit, growth potential
3. **Technical Recruiter Score** — required/preferred skills coverage, experience level, red flags

Candidate context:
{candidate_context_short}

Be honest and strict.

Return ONLY valid JSON (no markdown, no code fences):
{{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "best_resume": "<sre_devops or fullstack>",
    "reasoning": "<2-3 sentences>",
    "key_matches": ["<skill1>", ...],
    "gaps": ["<gap1>", ...],
    "tailoring_suggestions": ["<suggestion1>", ...]
}}"""


# Pre-built prompts for backward compatibility (used when no UserProfile is provided)
MATCH_SYSTEM_PROMPT = _build_match_system_prompt(_DEFAULT_CANDIDATE_CONTEXT)
SINGLE_MATCH_SYSTEM_PROMPT = _build_single_match_system_prompt(_DEFAULT_CANDIDATE_CONTEXT_SHORT)


def extract_json(text: str):
    """Robustly extract JSON from LLM response text.

    Handles: markdown fences, trailing text, partial responses,
    arrays and objects.
    """
    text = text.strip()

    # Remove markdown code fences
    if "```" in text:
        # Find content between first and last ```
        parts = text.split("```")
        if len(parts) >= 3:
            inner = parts[1]
            # Strip language identifier (json, JSON, etc.)
            if inner.startswith(("json", "JSON")):
                inner = inner[4:]
            text = inner.strip()
        elif len(parts) == 2:
            # Only opening fence, no closing
            inner = parts[1]
            if inner.startswith(("json", "JSON")):
                inner = inner[4:]
            text = inner.strip()

    # Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON array
    array_match = re.search(r'\[[\s\S]*\]', text)
    if array_match:
        try:
            return json.loads(array_match.group())
        except json.JSONDecodeError:
            pass

    # Try to find a JSON object
    obj_match = re.search(r'\{[\s\S]*\}', text)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


def _format_job_for_prompt(job: Job, index: int) -> str:
    """Format a single job for inclusion in a batch prompt."""
    desc = job.description[:2500] if job.description else "(No description available)"
    return f"""--- JOB {index} ---
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.remote}
- Salary: {job.salary or 'Not specified'}
- Type: {job.job_type or 'Not specified'}

Description:
{desc}"""


def _apply_scores(job: Job, result: dict, min_score: int) -> bool:
    """Apply parsed scores to a job object. Returns True if it passes."""
    ats = result.get("ats_score", 0)
    hm = result.get("hiring_manager_score", 0)
    tr = result.get("tech_recruiter_score", 0)
    avg_score = round((ats + hm + tr) / 3, 1)

    job.ats_score = ats
    job.hiring_manager_score = hm
    job.tech_recruiter_score = tr
    job.match_score = avg_score
    job.match_reasoning = result.get("reasoning", "")
    job.matched_resume = result.get("best_resume", "fullstack")
    job._match_data = result

    if avg_score >= min_score:
        logger.info(f"[MATCH] {job.title} @ {job.company} — ATS={ats} HM={hm} TR={tr} (avg={avg_score}) -> {job.matched_resume}")
        return True
    else:
        logger.info(f"[SKIP]  {job.title} @ {job.company} — ATS={ats} HM={hm} TR={tr} (avg={avg_score})")
        return False


def _match_batch(
    batch: List[Job],
    resume_context: str,
    ai_client: AIClient,
    min_score: int,
    batch_num: int,
    system_prompt: str = "",
    single_system_prompt: str = "",
    candidate_info: str = "",
) -> List[Job]:
    """Score a batch of jobs in a single AI call."""
    matched = []
    system_prompt = system_prompt or MATCH_SYSTEM_PROMPT
    single_system_prompt = single_system_prompt or SINGLE_MATCH_SYSTEM_PROMPT
    candidate_info = candidate_info or _DEFAULT_CANDIDATE_INFO

    jobs_text = "\n\n".join(
        _format_job_for_prompt(job, i) for i, job in enumerate(batch)
    )

    user_prompt = f"""Evaluate these {len(batch)} jobs from all 3 perspectives:

{jobs_text}

CANDIDATE RESUMES:
{resume_context}

CANDIDATE INFO:
{candidate_info}

Return a JSON array with {len(batch)} objects, one per job in order."""

    try:
        info = ai_client.complete_with_info(
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.3,
        )
        result_text = info["response"]

        results = extract_json(result_text)

        # Handle case where model returns a single object instead of array
        if isinstance(results, dict):
            results = [results]

        for i, job in enumerate(batch):
            if i < len(results):
                result = results[i]
                if _apply_scores(job, result, min_score):
                    matched.append(job)
                # Record which model did the matching
                job.match_provider = info["provider"]
                job.match_model = info["model"]
                log_quality(task="match", provider=info["provider"], model=info["model"], job_id=job.job_id, company=job.company, job_title=job.title, scores={"ats_score": job.ats_score, "hiring_manager_score": job.hiring_manager_score, "tech_recruiter_score": job.tech_recruiter_score})
            else:
                logger.warning(f"Batch {batch_num}: no result for job {i} ({job.title})")

    except json.JSONDecodeError as e:
        logger.warning(f"Batch {batch_num} JSON parse failed: {e}")
        logger.info("Falling back to single-job matching for this batch...")
        for job in batch:
            result = _match_single(job, resume_context, ai_client,
                                   system_prompt=single_system_prompt,
                                   candidate_info=candidate_info)
            if result and _apply_scores(job, result, min_score):
                matched.append(job)
            if job.match_provider:
                log_quality(task="match", provider=job.match_provider, model=job.match_model, job_id=job.job_id, company=job.company, job_title=job.title, scores={"ats_score": job.ats_score, "hiring_manager_score": job.hiring_manager_score, "tech_recruiter_score": job.tech_recruiter_score})

    except Exception as e:
        logger.error(f"Batch {batch_num} failed: {e}")
        # Fallback to single matching
        for job in batch:
            try:
                result = _match_single(job, resume_context, ai_client,
                                       system_prompt=single_system_prompt,
                                       candidate_info=candidate_info)
                if result and _apply_scores(job, result, min_score):
                    matched.append(job)
                if job.match_provider:
                    log_quality(task="match", provider=job.match_provider, model=job.match_model, job_id=job.job_id, company=job.company, job_title=job.title, scores={"ats_score": job.ats_score, "hiring_manager_score": job.hiring_manager_score, "tech_recruiter_score": job.tech_recruiter_score})
            except Exception as inner_e:
                logger.error(f"Single match failed for {job.title}: {inner_e}")

    return matched


def _match_single(
    job: Job,
    resume_context: str,
    ai_client: AIClient,
    system_prompt: str = "",
    candidate_info: str = "",
) -> dict | None:
    """Score a single job (fallback when batch fails)."""
    system_prompt = system_prompt or SINGLE_MATCH_SYSTEM_PROMPT
    candidate_info = candidate_info or _DEFAULT_CANDIDATE_INFO_SHORT

    desc = job.description[:4000] if job.description else "(No description available)"
    user_prompt = f"""Evaluate this job match from all 3 perspectives:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.remote}
- Salary: {job.salary or 'Not specified'}
- Type: {job.job_type or 'Not specified'}

JOB DESCRIPTION:
{desc}

CANDIDATE RESUMES:
{resume_context}

CANDIDATE INFO:
{candidate_info}"""

    try:
        info = ai_client.complete_with_info(
            prompt=user_prompt,
            system=system_prompt,
            temperature=0.3,
        )
        result = extract_json(info["response"])
        # Record which model did the matching
        job.match_provider = info["provider"]
        job.match_model = info["model"]
        return result
    except Exception as e:
        logger.error(f"Failed to match {job.title}: {e}")
        return None


def match_jobs(
    jobs: List[Job],
    resumes: Dict[str, str],
    ai_client: AIClient,
    min_score: int = 60,
    batch_size: int = 5,
    user_profile: Optional["UserProfile"] = None,
) -> List[Job]:
    """Score and filter jobs using 3-perspective evaluation.

    Jobs are processed in batches of `batch_size` to reduce API calls.
    A job passes if the AVERAGE of (ATS, HM, TR) meets min_score.
    Returns matched jobs sorted by average score descending.

    Parameters
    ----------
    user_profile:
        Optional UserProfile instance. When provided, the candidate context
        in AI prompts is derived from the profile instead of using hardcoded
        defaults. Pass ``None`` to preserve the original single-user behavior.
    """
    matched_jobs = []

    # Build prompts — use user profile when available, else fall back to hardcoded defaults
    if user_profile is not None:
        candidate_ctx = user_profile.to_candidate_context()
        system_prompt = _build_match_system_prompt(candidate_ctx)
        single_system_prompt = _build_single_match_system_prompt(candidate_ctx)
        candidate_info = candidate_ctx  # reuse for inline CANDIDATE INFO block
    else:
        system_prompt = MATCH_SYSTEM_PROMPT
        single_system_prompt = SINGLE_MATCH_SYSTEM_PROMPT
        candidate_info = _DEFAULT_CANDIDATE_INFO

    # Combine resumes into context
    resume_context = ""
    for key, tex in resumes.items():
        resume_context += f"\n\n=== RESUME VARIANT: {key} ===\n{tex}\n"

    # Process in batches
    total_batches = (len(jobs) + batch_size - 1) // batch_size
    for batch_num in range(total_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, len(jobs))
        batch = jobs[start:end]

        logger.info(f"[Batch {batch_num + 1}/{total_batches}] Matching {len(batch)} jobs...")
        batch_matches = _match_batch(
            batch, resume_context, ai_client, min_score, batch_num + 1,
            system_prompt=system_prompt,
            single_system_prompt=single_system_prompt,
            candidate_info=candidate_info,
        )
        matched_jobs.extend(batch_matches)

    matched_jobs.sort(key=lambda j: j.match_score, reverse=True)
    return matched_jobs
