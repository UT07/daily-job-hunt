import logging
import os

import boto3

from ai_helper import ai_complete, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    light_touch = event.get("light_touch", False)

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Read job from jobs_raw
    job = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job.data:
        return {"error": f"Job {job_hash} not found"}
    job = job.data[0]

    # Get latest resume (no is_active column; use most recently created)
    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    if not resume.data:
        return {"error": "No resume found"}
    resume_tex = resume.data[0].get("tex_content", "")

    # Tailor using AI
    if light_touch:
        system_prompt = "You are a resume tailoring expert. Make MINIMAL changes: reorder skills to match JD keywords, tweak the summary sentence. Keep 95%+ of the original text. Return LaTeX."
    else:
        system_prompt = "You are a resume tailoring expert. Rewrite bullet points to emphasize relevant experience for this role. Reorder sections strategically. Return LaTeX."

    prompt = f"""Tailor this resume for the following job.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')[:3000]}

Resume LaTeX:
{resume_tex[:5000]}

Return ONLY the tailored LaTeX document. No explanation."""

    tailored_tex = ai_complete(prompt, system=system_prompt)

    # Write to S3
    tex_key = f"users/{user_id}/resumes/{job_hash}_tailored.tex"
    s3.put_object(Bucket=bucket, Key=tex_key, Body=tailored_tex.encode("utf-8"))

    # Update job record (only columns that exist)
    db.table("jobs").update({
        "resume_version": 1,
    }).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[tailor] {'Light' if light_touch else 'Full'} tailor for {job_hash}")
    return {"job_hash": job_hash, "tex_s3_key": tex_key, "user_id": user_id}
