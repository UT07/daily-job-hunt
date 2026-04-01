import json
import logging
import uuid
from datetime import datetime

import boto3

from ai_helper import ai_complete_cached, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


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
    for job in jobs:
        score_result = score_single_job(job, resume_tex)

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

    logger.info(f"[score_batch] {len(jobs)} scored -> {len(matched_items)} matched (min_score={min_score})")
    return {"matched_items": matched_items, "matched_count": len(matched_items)}


SCORE_SYSTEM_PROMPT = """You are an expert job-candidate evaluator. Score how well a candidate's resume matches a job listing from THREE distinct perspectives.

SCORING PERSPECTIVES (each 0-100):

1. **ATS Score** — keyword match, formatting, section structure, title alignment
2. **Hiring Manager Score** — relevant impact, experience narrative, culture fit, growth potential
3. **Technical Recruiter Score** — required/preferred skills coverage, experience level, red flags

Be honest and strict.

SCORING GUIDANCE FOR JUNIOR/GRADUATE ROLES:
- For roles marked as "Junior", "Graduate", "Entry Level", or "Associate": be MORE lenient with experience requirements.
- A strong portfolio and relevant coursework/projects can compensate for fewer years of experience.
- Do NOT penalize junior roles for listing technologies the candidate hasn't used.

Return ONLY valid JSON (no markdown, no code fences):
{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "match_score": <0-100 average>,
    "reasoning": "<2-3 sentences>",
    "key_matches": ["<skill1>", ...],
    "gaps": ["<gap1>", ...]
}"""


def score_single_job(job: dict, resume_tex: str) -> dict | None:
    """Score a single job against the user's resume using 3-perspective AI scoring.
    Uses the same prompt template as matcher.py for consistency."""
    prompt = f"""Score this job against the candidate's resume.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')[:2000]}

Resume (LaTeX): {resume_tex[:3000]}"""

    try:
        response = ai_complete_cached(prompt, system=SCORE_SYSTEM_PROMPT)
        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

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
