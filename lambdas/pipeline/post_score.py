"""Post-tailor scoring — quality gate that scores tailored resumes and flags for improvement.

Scores from 3 perspectives (ATS, Hiring Manager, Tech Recruiter).
If any score < 85 and improvement_round < 2, returns needs_improvement=True
so the state machine can re-tailor with specific feedback.
"""
import json
import logging
import os
import re

import boto3

from ai_helper import council_complete, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SCORER_SYSTEM_PROMPT = r"""You are an expert at evaluating tailored resumes against job listings from three perspectives.

Score 0-100 on each:

1. **ATS Score** — keyword match, section structure, formatting, job title alignment.
   Penalty: -15 if resume contains fabricated skills/metrics not in original.

2. **Hiring Manager Score** — relevant impact with metrics, career narrative, culture fit.
   Penalty: -10 for AI filler ("leveraging", "spearheaded", "showcasing").

3. **Technical Recruiter Score** — required skills coverage, experience level match, red flags.
   Penalty: -10 for listing technologies with no backing experience.

CALIBRATION:
- 95-100: Exceeds all requirements
- 85-94: Meets most requirements, minor gaps
- 70-84: Notable gaps in 1-2 areas
- 50-69: Significant gaps
- Below 50: Fundamental misalignment

Be strict. 85+ means "would pass this evaluator".
If below 85, provide SPECIFIC improvements (reference exact sections/bullets to change).
Improvements must NEVER fabricate — only reword, reorder, emphasize existing content.

Return ONLY valid JSON:
{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "improvements": ["<specific edit 1>", "<specific edit 2>", ...],
    "fabrication_detected": <true/false>
}"""


def _parse_scores(text: str) -> dict | None:
    """Parse JSON scores from AI response."""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Find JSON object
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        scores = json.loads(match.group())
        for key in ("ats_score", "hiring_manager_score", "tech_recruiter_score"):
            if key in scores:
                scores[key] = max(0, min(100, int(float(scores[key]))))
        return scores
    except (json.JSONDecodeError, ValueError):
        return None


def handler(event, context):
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")
    improvement_round = event.get("improvement_round", 0)

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Get resume from S3
    job_row = db.table("jobs").select("resume_s3_key").eq("user_id", user_id).eq("job_hash", job_hash).execute()
    if not job_row.data or not job_row.data[0].get("resume_s3_key"):
        logger.info(f"[post_score] No resume for {job_hash}, skipping")
        return {"job_hash": job_hash, "user_id": user_id, "scored": False, "reason": "no_resume"}

    resume_s3_key = job_row.data[0]["resume_s3_key"]

    try:
        tex_key = resume_s3_key.replace(".pdf", ".tex")
        tex_obj = s3.get_object(Bucket=bucket, Key=tex_key)
        tailored_tex = tex_obj["Body"].read().decode("utf-8")

        job_data = db.table("jobs_raw").select("description, title, company").eq("job_hash", job_hash).execute()
        if not job_data.data:
            return {"job_hash": job_hash, "user_id": user_id, "scored": False, "reason": "no_job_data"}

        job = job_data.data[0]
        description = job.get("description", "")

        # Score the tailored resume
        prompt = f"""Score this tailored resume against the job description.

Job: {job['title']} at {job['company']}
Description: {description[:3000]}

Tailored Resume (LaTeX):
{tailored_tex[:5000]}"""

        result = council_complete(
            prompt=prompt,
            system=SCORER_SYSTEM_PROMPT,
            task_description=f"Score resume for {job['title']} at {job['company']}. Be strict — 85+ means ready to submit.",
            n_generators=2,
            temperature=0.2,
        )

        scores = _parse_scores(result["content"])
        if not scores:
            logger.warning(f"[post_score] Could not parse scores for {job_hash}: {result['content'][:200]}")
            return {"job_hash": job_hash, "user_id": user_id, "scored": False, "reason": "parse_error"}

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)
        min_score = min(ats, hm, tr)
        fabrication = scores.get("fabrication_detected", False)
        improvements = scores.get("improvements", [])

        # Save scores to DB
        update = {
            "tailored_ats_score": ats,
            "tailored_hm_score": hm,
            "tailored_tr_score": tr,
        }
        db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()

        logger.info(f"[post_score] {job_hash}: ATS={ats}, HM={hm}, TR={tr}, min={min_score}, fabrication={fabrication}")

        # Quality gate: flag for re-tailor if below threshold
        needs_improvement = min_score < 85 and not fabrication and improvement_round < 2

        return {
            "job_hash": job_hash,
            "user_id": user_id,
            "scored": True,
            "scores": {"ats": ats, "hm": hm, "tr": tr},
            "needs_improvement": needs_improvement,
            "improvement_round": improvement_round,
            "improvements": improvements[:5],  # Cap at 5 suggestions
        }

    except Exception as e:
        logger.warning(f"[post_score] Failed for {job_hash}: {e}")
        return {"job_hash": job_hash, "user_id": user_id, "scored": False, "reason": str(e)[:200]}
