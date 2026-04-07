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

    if cover_letter_pdf_key:
        cl_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": cover_letter_pdf_key},
            ExpiresIn=2592000,
        )
        update["cover_letter_s3_url"] = cl_url

    # Score the tailored resume (before/after delta + writing quality)
    if resume_pdf_key:
        try:
            # Read the tailored .tex from S3
            tex_key = resume_pdf_key.replace(".pdf", ".tex")
            tex_obj = s3.get_object(Bucket=bucket, Key=tex_key)
            tailored_tex = tex_obj["Body"].read().decode("utf-8")

            # Get the job description for scoring
            job_row = db.table("jobs_raw").select("description, title, company").eq("job_hash", job_hash).execute()
            if job_row.data and tailored_tex:
                job_data = job_row.data[0]

                # Compute tailored scores (before/after comparison)
                from score_batch import compute_tailored_scores, score_writing_quality
                tailored_scores = compute_tailored_scores(job_data, tailored_tex)
                if tailored_scores:
                    update.update(tailored_scores)
                    logger.info(f"[save_job] Tailored scores for {job_hash}: {tailored_scores}")

                # Compute writing quality
                wq = score_writing_quality(tailored_tex)
                if wq.get("writing_quality_score") is not None:
                    update["writing_quality_score"] = wq["writing_quality_score"]
                    logger.info(f"[save_job] Writing quality for {job_hash}: {wq['writing_quality_score']}")
        except Exception as e:
            logger.warning(f"[save_job] Post-tailor scoring failed for {job_hash}: {e}")

    if update:
        update["application_status"] = "ready"
        db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[save_job] Updated {job_hash} with {len(update)} fields")
    return {"job_hash": job_hash, "user_id": user_id, "saved": True}
