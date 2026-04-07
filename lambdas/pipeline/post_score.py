"""Deferred post-tailor scoring — runs AFTER all jobs are saved.

Scores tailored resumes for before/after delta and writing quality.
Runs as a separate Map state so it doesn't block the SaveJob hot path.
"""
import logging
import os

import boto3

from ai_helper import get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Get the job's resume S3 key from the jobs table
    job_row = db.table("jobs").select("resume_s3_key").eq("user_id", user_id).eq("job_hash", job_hash).execute()
    if not job_row.data or not job_row.data[0].get("resume_s3_key"):
        logger.info(f"[post_score] No resume for {job_hash}, skipping")
        return {"job_hash": job_hash, "scored": False, "reason": "no_resume"}

    resume_s3_key = job_row.data[0]["resume_s3_key"]

    try:
        tex_key = resume_s3_key.replace(".pdf", ".tex")
        tex_obj = s3.get_object(Bucket=bucket, Key=tex_key)
        tailored_tex = tex_obj["Body"].read().decode("utf-8")

        job_data = db.table("jobs_raw").select("description, title, company").eq("job_hash", job_hash).execute()
        if not job_data.data or not tailored_tex:
            return {"job_hash": job_hash, "scored": False, "reason": "no_job_data"}

        from score_batch import compute_tailored_scores, score_writing_quality

        update = {}
        tailored_scores = compute_tailored_scores(job_data.data[0], tailored_tex)
        if tailored_scores:
            update.update(tailored_scores)

        wq = score_writing_quality(tailored_tex)
        if wq.get("writing_quality_score") is not None:
            update["writing_quality_score"] = wq["writing_quality_score"]

        if update:
            db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()
            logger.info(f"[post_score] Scored {job_hash}: {update}")

        return {"job_hash": job_hash, "scored": True}

    except Exception as e:
        logger.warning(f"[post_score] Failed for {job_hash}: {e}")
        return {"job_hash": job_hash, "scored": False, "reason": str(e)[:200]}
