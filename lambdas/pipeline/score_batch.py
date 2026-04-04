import json
import logging
import random
import statistics
import uuid
from datetime import datetime


from ai_helper import ai_complete_cached, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def should_skip_scoring(job: dict) -> str | None:
    """Check if job should be skipped for scoring. Returns score_status or None."""
    desc = job.get("description", "") or ""
    if len(desc) < 100:
        return "insufficient_data"
    company = job.get("company", "") or ""
    if not company.strip():
        return "incomplete"
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

    resume_tex = resume_result.data[0].get("tex_content", "")
    if not resume_tex:
        logger.warning(f"[score_batch] Resume tex_content is empty for user {user_id}")
        return {"matched_items": [], "matched_count": 0, "error": "no_resume"}

    matched_items = []
    skipped_count = 0
    for job in jobs:
        skip_status = should_skip_scoring(job)
        if skip_status:
            logger.info(f"[score_batch] Skipping {job['job_hash']}: {skip_status}")
            skipped_count += 1
            continue

        score_result = score_single_job_deterministic(job, resume_tex)

        if score_result is None:
            continue

        match_score = score_result.get("match_score", 0)
        if match_score < min_score:
            continue

        job_record = {
            "job_id": str(uuid.uuid4()),
            "user_id": user_id,
            "job_hash": job["job_hash"],
            "title": job["title"],
            "company": job["company"],
            "description": job.get("description"),
            "location": job.get("location"),
            "apply_url": job.get("apply_url"),
            "source": job["source"],
            "match_score": match_score,
            "ats_score": score_result.get("ats_score", 0),
            "hiring_manager_score": score_result.get("hiring_manager_score", 0),
            "tech_recruiter_score": score_result.get("tech_recruiter_score", 0),
            "key_matches": score_result.get("key_matches", []),
            "gaps": score_result.get("gaps", []),
            "match_reasoning": score_result.get("reasoning", ""),
            "tailoring_model": f"{score_result.get('provider', 'council')}:{score_result.get('model', 'consensus')}",
            "first_seen": datetime.utcnow().isoformat(),
        }
        try:
            db.table("jobs").insert(job_record).execute()
        except Exception as e:
            # Retry without optional columns if they don't exist yet
            if "column" in str(e) and "does not exist" in str(e):
                for col in ("key_matches", "gaps", "match_reasoning"):
                    job_record.pop(col, None)
                try:
                    db.table("jobs").insert(job_record).execute()
                except Exception as e2:
                    logger.warning(f"[score_batch] Insert retry failed for {job['job_hash']}: {e2}")
                    continue
            else:
                logger.warning(f"[score_batch] Insert failed for {job['job_hash']}: {e}")
                continue

        light_touch = match_score >= 85
        matched_items.append({
            "job_hash": job["job_hash"],
            "user_id": user_id,
            "light_touch": light_touch,
        })

    logger.info(f"[score_batch] {len(jobs)} fetched, {skipped_count} skipped, {len(matched_items)} matched (min_score={min_score})")
    return {"matched_items": matched_items, "matched_count": len(matched_items), "skipped_count": skipped_count}


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

Return ONLY valid JSON (no markdown, no code fences):
{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "match_score": <0-100 weighted average>,
    "reasoning": "<2-3 sentences explaining the scores and key factors>",
    "key_matches": ["<skill1>", "<skill2>", ...],
    "gaps": ["<missing_skill1>", "<missing_experience1>", ...]
}"""


def score_single_job(job: dict, resume_tex: str, temperature: float = 0) -> dict | None:
    """Score a single job against the user's resume using 3-perspective AI scoring.
    Uses the same prompt template as matcher.py for consistency.

    Parameters
    ----------
    temperature:
        Sampling temperature for the AI call. Default 0 for deterministic scoring.
    """
    prompt = f"""Score this job against the candidate's resume.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')}

Resume (LaTeX): {resume_tex}"""

    try:
        response_dict = ai_complete_cached(
            prompt, system=SCORE_SYSTEM_PROMPT, temperature=temperature
        )
        text = response_dict["content"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

        # Include model info so we can save it to DB
        result["provider"] = response_dict.get("provider", "council")
        result["model"] = response_dict.get("model", "auto")

        # Ensure match_score is computed consistently
        if "match_score" not in result:
            ats = result.get("ats_score", 0)
            hm = result.get("hiring_manager_score", 0)
            tr = result.get("tech_recruiter_score", 0)
            result["match_score"] = round((ats + hm + tr) / 3)

        return result
    except json.JSONDecodeError as e:
        logger.error(f"[score_batch] JSON parse error for {job['job_hash']}: {e}")
        return None
    except Exception as e:
        logger.error(f"[score_batch] AI scoring failed for {job['job_hash']}: {e}")
        return None


def score_single_job_deterministic(
    job: dict, resume_tex: str, num_calls: int = 3
) -> dict | None:
    """Score a job multiple times with temp=0 and take the median of each perspective.

    This dampens remaining provider variance by making ``num_calls`` independent
    scoring calls and returning the median of each score dimension. The result is
    *not* cached itself — individual ``score_single_job`` calls hit the cache as
    usual, so callers should bust the cache (or use ``skip_cache``) if they want
    truly independent calls.

    Returns None only when *all* calls fail.
    """
    all_scores: list[dict] = []
    for _ in range(num_calls):
        result = score_single_job(job, resume_tex, temperature=0)
        if result is not None:
            all_scores.append(result)

    if not all_scores:
        return None
    if len(all_scores) == 1:
        return all_scores[0]

    # Use first result as base for non-numeric fields (reasoning, key_matches,
    # gaps, provider, model), then overwrite numeric fields with medians so the
    # result dict keeps the same shape as ``score_single_job``.
    merged = dict(all_scores[0])
    merged.update({
        "ats_score": int(statistics.median([s["ats_score"] for s in all_scores])),
        "hiring_manager_score": int(
            statistics.median([s["hiring_manager_score"] for s in all_scores])
        ),
        "tech_recruiter_score": int(
            statistics.median([s["tech_recruiter_score"] for s in all_scores])
        ),
        "match_score": round(
            statistics.median([s.get("match_score", 0) for s in all_scores]), 1
        ),
    })
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
