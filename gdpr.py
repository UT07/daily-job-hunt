"""GDPR compliance utilities: consent, data export, account deletion."""

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def record_consent(db, user_id: str) -> dict:
    """Record GDPR consent timestamp for a user."""
    return db.update_user(user_id, {"gdpr_consent_at": datetime.utcnow().isoformat()})


def _all_user_rows(db, table: str, user_id: str, columns: str = "*", order: str = "created_at") -> list:
    """Every row of `table` owned by `user_id`, walked a page at a time.

    Uncounted on purpose. A `count="exact"` range that starts past the last
    row is a 416 PGRST103 from PostgREST, not an empty page -- which is how
    the export 500'd for a user with one job (2026-10-09). `_all_rows` stops
    on a short page, so it never asks for a range beyond the end.
    """
    return db._all_rows(
        lambda lo, hi: db.client.table(table).select(columns)
        .eq("user_id", user_id).order(order).range(lo, hi)
    )


def export_user_data(db, user_id: str) -> bytes:
    """Export all user data as a ZIP file (GDPR Article 15 - Right of Access).

    Returns ZIP file bytes containing:
    - profile.json (user profile)
    - resumes.json (all resumes)
    - search_config.json
    - jobs.json (every job, archived ones included)
    - timeline.json (application status history)
    - resume_versions.json
    - runs.json (all pipeline runs)

    Jobs are NOT read through `db.get_jobs`: that is the dashboard query. It
    returns `(rows, total)`, counts exactly (so paging past the end raises),
    and hides jobs older than 30 days by default. An export is none of those.
    """
    from db_client import JOB_LIST_COLUMNS

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Profile
        user = db.get_user(user_id)
        if user:
            # Remove internal fields
            user.pop("gdpr_deletion_requested_at", None)
            zf.writestr("profile.json", json.dumps(user, indent=2, default=str))

        # Resumes
        resumes = db.get_resumes(user_id)
        zf.writestr("resumes.json", json.dumps(resumes, indent=2, default=str))

        # Search config
        config = db.get_search_config(user_id)
        if config:
            zf.writestr("search_config.json", json.dumps(config, indent=2, default=str))

        # Every column except the embedding vector (internal, ~1 MB/100 rows).
        jobs = _all_user_rows(db, "jobs", user_id, JOB_LIST_COLUMNS, order="first_seen")
        zf.writestr("jobs.json", json.dumps(jobs, indent=2, default=str))

        timeline = _all_user_rows(db, "application_timeline", user_id)
        zf.writestr("timeline.json", json.dumps(timeline, indent=2, default=str))

        versions = _all_user_rows(db, "resume_versions", user_id)
        zf.writestr("resume_versions.json", json.dumps(versions, indent=2, default=str))

        runs = _all_user_rows(db, "runs", user_id, order="run_date")
        zf.writestr("runs.json", json.dumps(runs, indent=2, default=str))

    zip_buffer.seek(0)
    logger.info(
        "[GDPR] Exported data for user %s: %d jobs, %d timeline events, %d resume versions, %d runs",
        user_id, len(jobs), len(timeline), len(versions), len(runs),
    )
    return zip_buffer.read()


def request_deletion(db, user_id: str) -> dict:
    """Soft-delete: mark account for deletion in 30 days (GDPR Article 17)."""
    return db.update_user(user_id, {
        "gdpr_deletion_requested_at": datetime.utcnow().isoformat()
    })


def cancel_deletion(db, user_id: str) -> dict:
    """Cancel a pending deletion request."""
    return db.update_user(user_id, {"gdpr_deletion_requested_at": None})


def hard_delete_user(db, user_id: str, s3_client=None, drive_service=None):
    """Permanently delete all user data (called by data retention job after 30 days).

    Deletes from: Postgres (CASCADE handles related tables), S3, Google Drive.
    """
    logger.info(f"[GDPR] Hard-deleting user {user_id}")

    # 1. Delete S3 artifacts if configured
    if s3_client:
        try:
            import boto3
            bucket = s3_client  # pass bucket name as string
            s3 = boto3.client("s3")
            prefix = f"users/{user_id}/"
            response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            objects = response.get("Contents", [])
            if objects:
                s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]}
                )
                logger.info(f"[GDPR] Deleted {len(objects)} S3 objects for user {user_id}")
        except Exception as e:
            logger.error(f"[GDPR] S3 deletion failed for user {user_id}: {e}")

    # 2. Delete from Postgres (CASCADE handles jobs, runs, resumes, search_configs, audit_log)
    db.client.table("users").delete().eq("id", user_id).execute()
    logger.info(f"[GDPR] Hard-deleted user {user_id} from database")
