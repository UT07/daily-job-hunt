import logging
import os

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
    job_hash = event["job_hash"]
    user_id = event["user_id"]

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    job = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job.data:
        return {"error": f"Job {job_hash} not found"}
    job = job.data[0]

    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .eq("is_active", True).execute()
    resume_text = resume.data[0].get("resume_text", "") if resume.data else ""

    prompt = f"""Write a cover letter for this job application.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')[:3000]}

Candidate Resume: {resume_text[:3000]}

Return ONLY a LaTeX document for the cover letter. Professional format, one page."""

    from ai_client import get_ai_response
    cover_letter_tex = get_ai_response(prompt, system="You are a cover letter expert. Return only LaTeX.")

    tex_key = f"users/{user_id}/cover_letters/{job_hash}_cover.tex"
    s3.put_object(Bucket=bucket, Key=tex_key, Body=cover_letter_tex.encode("utf-8"))

    logger.info(f"[cover_letter] Generated for {job_hash}")
    return {"job_hash": job_hash, "tex_s3_key": tex_key, "user_id": user_id, "doc_type": "cover_letter"}
