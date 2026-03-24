"""S3 upload module for job automation pipeline.

Uploads compiled PDF artifacts (resumes + cover letters) to S3 and returns
presigned URLs with 30-day expiry for secure, temporary sharing.

S3 structure (single-user / default):
    job-hunt/{date}/resumes/{filename}.pdf
    job-hunt/{date}/cover-letters/{filename}.pdf

S3 structure (multi-tenant, when user_id is provided):
    users/{user_id}/{date}/resumes/{filename}.pdf
    users/{user_id}/{date}/cover-letters/{filename}.pdf

Required env vars:
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    S3_BUCKET_NAME
    AWS_REGION (defaults to eu-west-1)
"""

from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from scrapers.base import Job

logger = logging.getLogger(__name__)

# Presigned URL expiry: 7 days (S3 maximum for IAM user credentials)
PRESIGN_EXPIRY = 7 * 24 * 60 * 60


def _get_s3_client():
    """Create a boto3 S3 client from environment variables."""
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        region_name=os.environ.get("AWS_REGION", "eu-west-1"),
    )


def _s3_prefix(run_date: str, user_id: Optional[str] = None) -> str:
    """Return the S3 key prefix, optionally namespaced by user_id.

    When ``user_id`` is provided the path becomes ``users/{user_id}/{date}``
    for multi-tenant isolation. Otherwise the legacy ``job-hunt/{date}`` path
    is used for backward compatibility.
    """
    if user_id:
        return f"users/{user_id}/{run_date}"
    return f"job-hunt/{run_date}"


def upload_file(local_path: str, s3_key: str, bucket: str) -> Optional[str]:
    """Upload a single file to S3 and return a presigned URL.

    Returns the presigned URL on success, or None on failure.
    """
    client = _get_s3_client()
    try:
        client.upload_file(
            local_path,
            bucket,
            s3_key,
            ExtraArgs={"ContentType": "application/pdf"},
        )
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=PRESIGN_EXPIRY,
        )
        logger.info(f"[S3] Uploaded {Path(local_path).name} -> s3://{bucket}/{s3_key}")
        return url
    except ClientError as e:
        logger.error(f"[S3] Failed to upload {local_path}: {e}")
        return None


def upload_artifacts(
    matched_jobs: List[Job],
    run_date: str,
    user_id: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Upload all PDF artifacts for matched jobs to S3.

    S3 structure organized by date and resume type for easy browsing:
        {prefix}/{resume_type}/resumes/{filename}.pdf
        {prefix}/{resume_type}/cover-letters/{filename}.pdf

    When ``user_id`` is provided, prefix = ``users/{user_id}/{date}``.
    Otherwise prefix = ``job-hunt/{date}`` (legacy single-user path).

    Example (multi-tenant):
        users/abc-123/2026-03-19/sre_devops/resumes/Utkarsh_Singh_SRE_RedHat_2026-03-19.pdf

    Example (legacy):
        job-hunt/2026-03-19/sre_devops/resumes/Utkarsh_Singh_SRE_RedHat_2026-03-19.pdf

    Args:
        matched_jobs: List of Job objects with tailored_pdf_path / cover_letter_pdf_path set.
        run_date: Date string (YYYY-MM-DD) for S3 path organization.
        user_id: Optional user ID for multi-tenant path namespacing.

    Returns:
        Dict mapping job_id -> {"resume_url": str, "cover_letter_url": str}.
        URLs are presigned with 7-day expiry.
    """
    bucket = os.environ.get("S3_BUCKET_NAME", "")
    if not bucket:
        logger.warning("[S3] S3_BUCKET_NAME not set, skipping upload")
        return {}

    prefix = _s3_prefix(run_date, user_id)
    results: Dict[str, Dict[str, str]] = {}

    for job in matched_jobs:
        job_urls: Dict[str, str] = {"resume_url": "", "cover_letter_url": ""}
        resume_type = job.matched_resume or "general"

        # Upload resume PDF
        if job.tailored_pdf_path and Path(job.tailored_pdf_path).exists():
            filename = Path(job.tailored_pdf_path).name
            s3_key = f"{prefix}/{resume_type}/resumes/{filename}"
            url = upload_file(job.tailored_pdf_path, s3_key, bucket)
            if url:
                job_urls["resume_url"] = url

        # Upload cover letter PDF
        if job.cover_letter_pdf_path and Path(job.cover_letter_pdf_path).exists():
            filename = Path(job.cover_letter_pdf_path).name
            s3_key = f"{prefix}/{resume_type}/cover-letters/{filename}"
            url = upload_file(job.cover_letter_pdf_path, s3_key, bucket)
            if url:
                job_urls["cover_letter_url"] = url

        if job_urls["resume_url"] or job_urls["cover_letter_url"]:
            results[job.job_id] = job_urls

    uploaded_resumes = sum(1 for v in results.values() if v["resume_url"])
    uploaded_cls = sum(1 for v in results.values() if v["cover_letter_url"])
    logger.info(f"[S3] Upload complete: {uploaded_resumes} resumes, {uploaded_cls} cover letters")

    return results


def upload_tracker(
    tracker_path: str,
    run_date: str,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Upload the Excel tracker to S3.

    Keeps both a dated version and a 'latest' version for easy access.
    When ``user_id`` is provided, paths are namespaced under ``users/{user_id}/``.
    Returns the presigned URL for the latest version.
    """
    bucket = os.environ.get("S3_BUCKET_NAME", "")
    if not bucket or not Path(tracker_path).exists():
        return None

    prefix = _s3_prefix(run_date, user_id)
    # For the 'latest' key, use the user-namespaced root if applicable
    if user_id:
        latest_prefix = f"users/{user_id}"
    else:
        latest_prefix = "job-hunt"

    client = _get_s3_client()
    try:
        # Upload dated version
        dated_key = f"{prefix}/job_tracker_{run_date}.xlsx"
        client.upload_file(
            tracker_path, bucket, dated_key,
            ExtraArgs={"ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )

        # Upload as 'latest' (always overwritten)
        latest_key = f"{latest_prefix}/job_tracker_latest.xlsx"
        client.upload_file(
            tracker_path, bucket, latest_key,
            ExtraArgs={"ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )

        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": latest_key},
            ExpiresIn=PRESIGN_EXPIRY,
        )
        logger.info(f"[S3] Tracker uploaded -> s3://{bucket}/{latest_key}")
        return url
    except ClientError as e:
        logger.error(f"[S3] Failed to upload tracker: {e}")
        return None
