import json
import logging
from datetime import datetime, timedelta

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def handler(event, context):
    user_id = event["user_id"]
    job_hashes = event.get("new_job_hashes", [])
    min_score = event.get("min_match_score", 60)

    if not job_hashes:
        return {"matched_items": [], "matched_count": 0}

    db = get_supabase()

    # Read jobs from jobs_raw
    jobs = []
    for h in job_hashes:
        result = db.table("jobs_raw").select("*").eq("job_hash", h).execute()
        if result.data:
            jobs.append(result.data[0])

    # Read user's active resume
    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .eq("is_active", True).execute()
    resume_text = resume.data[0]["resume_text"] if resume.data else ""

    if not resume_text:
        logger.warning(f"[score_batch] No active resume for user {user_id}")
        return {"matched_items": [], "matched_count": 0, "error": "no_resume"}

    # Score each job using AI (batches of 5)
    matched_items = []
    for i in range(0, len(jobs), 5):
        batch = jobs[i:i+5]
        for job in batch:
            # Call AI scoring
            score_result = score_single_job(job, resume_text, db)

            if score_result and score_result.get("match_score", 0) >= min_score:
                # Write scored job to jobs table
                job_record = {
                    "user_id": user_id,
                    "job_hash": job["job_hash"],
                    "title": job["title"],
                    "company": job["company"],
                    "description": job["description"],
                    "location": job.get("location"),
                    "apply_url": job.get("apply_url"),
                    "source": job["source"],
                    "match_score": score_result["match_score"],
                    "ats_score": score_result.get("ats_score", 0),
                    "hiring_manager_score": score_result.get("hiring_manager_score", 0),
                    "tech_recruiter_score": score_result.get("tech_recruiter_score", 0),
                    "matched_resume": resume_text[:100],
                    "first_seen": datetime.utcnow().isoformat(),
                }
                db.table("jobs").insert(job_record).execute()

                light_touch = score_result["match_score"] >= 85
                matched_items.append({
                    "job_hash": job["job_hash"],
                    "user_id": user_id,
                    "light_touch": light_touch,
                })

    logger.info(f"[score_batch] {len(jobs)} scored → {len(matched_items)} matched (min_score={min_score})")
    return {"matched_items": matched_items, "matched_count": len(matched_items)}


def score_single_job(job, resume_text, db):
    """Score a single job against user's resume using AI."""
    import hashlib

    # Check AI cache
    cache_key = hashlib.md5(f"score|{job['job_hash']}|{resume_text[:200]}".encode()).hexdigest()
    cached = db.table("ai_cache").select("response") \
        .eq("cache_key", cache_key) \
        .gte("expires_at", datetime.utcnow().isoformat()).execute()
    if cached.data:
        return json.loads(cached.data[0]["response"])

    # Call AI for scoring
    prompt = f"""Score this job against the candidate's resume.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')[:2000]}

Resume: {resume_text[:3000]}

Return JSON with: match_score (0-100), ats_score (0-100), hiring_manager_score (0-100), tech_recruiter_score (0-100), reasoning (string).
"""

    from ai_client import get_ai_response
    try:
        response = get_ai_response(prompt, system="You are a job matching AI. Return only valid JSON.")
        result = json.loads(response)

        # Cache the result
        db.table("ai_cache").upsert({
            "cache_key": cache_key,
            "response": json.dumps(result),
            "provider": "groq",
            "model": "auto",
            "expires_at": (datetime.utcnow() + timedelta(hours=72)).isoformat(),
        }, on_conflict="cache_key").execute()

        return result
    except Exception as e:
        logger.error(f"[score_batch] AI scoring failed for {job['job_hash']}: {e}")
        return None
