import logging
import os

import boto3

from ai_helper import get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# compile_latex returns this error_type when the tectonic binary is absent
# (local dev / unit tests). Treated as "no compile attempted", not a failure.
_LOCAL_DEV_ERROR = "tectonic_not_available"

# Truncate stored failure_reason so noisy stderr can't blow up the row.
_FAILURE_REASON_MAX = 500


def _compile_failure_reason(compile_result):
    """Extract a human-readable failure reason from a compile_latex error dict.

    Returns None when the result is healthy or only flags the local-dev case.
    """
    if not compile_result:
        return None
    if compile_result.get("pdf_s3_key"):
        return None
    error_type = compile_result.get("error")
    if not error_type or error_type == _LOCAL_DEV_ERROR:
        return None
    detail = compile_result.get("stderr") or ""
    reason = f"{error_type}: {detail}" if detail else error_type
    return reason[:_FAILURE_REASON_MAX]


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

    resume_failure_reason = _compile_failure_reason(event.get("compile_result"))
    cover_failure_reason = _compile_failure_reason(event.get("cover_compile_result"))

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

    # application_status is the USER's column — what they did with the job
    # (New / Applied / Interview / ...). This function used to write "failed",
    # "ready" and "scored" into it, which are pipeline states, not user
    # actions. Two consequences, both live on 2026-09-28:
    #
    #   1. Those values are not in app.py's _VALID_STATUSES, so the API would
    #      400 a user setting them while the pipeline wrote them freely, and
    #      the dashboard's Status filter could never match the ~68% of rows
    #      holding one.
    #   2. Writing one ERASED a real user status. A job the user marked
    #      "Applied" reverted to "ready" on the next pipeline run.
    #
    # All three were already derivable, so nothing is lost by not writing them:
    #   failed -> failure_reason IS NOT NULL
    #   ready  -> resume_s3_url IS NOT NULL
    #   scored -> match_score IS NOT NULL
    if resume_failure_reason:
        update["failure_reason"] = resume_failure_reason
        logger.error(f"[save_job] {job_hash} compile failed: {resume_failure_reason}")
    elif resume_pdf_key:
        # Clear any prior failure on a successful re-run.
        update["failure_reason"] = None

    if cover_failure_reason:
        logger.warning(f"[save_job] {job_hash} cover-letter compile failed (non-fatal): {cover_failure_reason}")

    if not update:
        # Previously this branch was avoided by writing application_status
        # "scored" purely so the dict was non-empty. Nothing to say is a valid
        # outcome; an empty PATCH is not.
        # Same shape as the normal return — a caller should not have to know
        # which branch ran to read the result.
        logger.info(f"[save_job] {job_hash}: nothing to update")
        return {
            "job_hash": job_hash,
            "user_id": user_id,
            "saved": False,
            "has_resume": False,
            "failed": False,
        }

    db.table("jobs").update(update).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(f"[save_job] Updated {job_hash} with {len(update)} fields: {sorted(update)}")
    return {
        "job_hash": job_hash,
        "user_id": user_id,
        "saved": True,
        "has_resume": bool(resume_pdf_key),
        "failed": bool(resume_failure_reason),
    }
