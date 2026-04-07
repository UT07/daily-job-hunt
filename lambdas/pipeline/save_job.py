import logging
import os

import boto3

from ai_helper import get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")

    # Extract PDF keys from accumulated step results — may not exist if upstream steps failed
    resume_pdf_key = None
    cover_letter_pdf_key = None

    if "compile_result" in event:
        resume_pdf_key = event["compile_result"].get("pdf_s3_key")
    elif "resume_pdf_s3_key" in event:
        resume_pdf_key = event["resume_pdf_s3_key"]

    if "cover_compile_result" in event:
        cover_letter_pdf_key = event["cover_compile_result"].get("pdf_s3_key")
    elif "cover_letter_pdf_s3_key" in event:
        cover_letter_pdf_key = event["cover_letter_pdf_s3_key"]

    s3 = boto3.client("s3")
    db = get_supabase()
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    update = {}

    if resume_pdf_key:
        resume_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": resume_pdf_key},
            ExpiresIn=2592000,
        )
        update["resume_s3_url"] = resume_url
        update["resume_s3_key"] = resume_pdf_key

    if cover_letter_pdf_key:
        cl_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": cover_letter_pdf_key},
            ExpiresIn=2592000,
        )
        update["cover_letter_s3_url"] = cl_url

    # Always set application_status — even if no PDFs were generated (e.g. called from SaveJobAfterError)
    if resume_pdf_key:
        update["application_status"] = "ready"
    elif not update:
        update["application_status"] = "scored"

    db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[save_job] Updated {job_hash} with {len(update)} fields (status={update.get('application_status')})")
    return {"job_hash": job_hash, "user_id": user_id, "saved": True, "has_resume": bool(resume_pdf_key)}
