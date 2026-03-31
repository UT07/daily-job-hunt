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
    resume_pdf_key = event.get("resume_pdf_s3_key")
    cover_letter_pdf_key = event.get("cover_letter_pdf_s3_key")

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

    if cover_letter_pdf_key:
        cl_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": cover_letter_pdf_key},
            ExpiresIn=2592000,
        )
        update["cover_letter_s3_url"] = cl_url

    if update:
        update["application_status"] = "ready"
        db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[save_job] Updated {job_hash} with {len(update)} fields")
    return {"job_hash": job_hash, "user_id": user_id, "saved": True}
