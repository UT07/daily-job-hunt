#!/usr/bin/env python3
"""Data retention automation — daily cleanup job.

Enforces data retention policies:
1. Purge job listings older than 90 days
2. Delete S3 PDFs older than 30 days
3. Hard-delete users who requested deletion >30 days ago
4. Clean up old audit log entries (>1 year)

Run daily via cron or GitHub Actions:
    python scripts/data_retention.py
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="[%(levelname).1s] %(message)s")
logger = logging.getLogger(__name__)


def purge_old_jobs(db, days: int = 90):
    """Delete job listings older than N days for all users."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    result = (
        db.client.table("jobs")
        .delete()
        .lt("first_seen", cutoff)
        .execute()
    )
    count = len(result.data) if result.data else 0
    logger.info(f"[RETENTION] Purged {count} jobs older than {days} days")
    return count


def purge_old_s3_artifacts(bucket_name: str, days: int = 30):
    """Delete S3 objects older than N days."""
    try:
        import boto3
        s3 = boto3.client("s3")
        cutoff = datetime.utcnow() - timedelta(days=days)

        paginator = s3.get_paginator("list_objects_v2")
        deleted = 0

        for page in paginator.paginate(Bucket=bucket_name, Prefix="users/"):
            objects = page.get("Contents", [])
            old_objects = [
                {"Key": obj["Key"]}
                for obj in objects
                if obj["LastModified"].replace(tzinfo=None) < cutoff
            ]
            if old_objects:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": old_objects}
                )
                deleted += len(old_objects)

        logger.info(f"[RETENTION] Deleted {deleted} S3 objects older than {days} days")
        return deleted
    except Exception as e:
        logger.error(f"[RETENTION] S3 cleanup failed: {e}")
        return 0


def hard_delete_expired_users(db, grace_days: int = 30):
    """Hard-delete users whose deletion was requested >grace_days ago."""
    cutoff = (datetime.utcnow() - timedelta(days=grace_days)).isoformat()

    # Find users with deletion requested before cutoff
    result = (
        db.client.table("users")
        .select("id, email")
        .not_.is_("gdpr_deletion_requested_at", "null")
        .lt("gdpr_deletion_requested_at", cutoff)
        .execute()
    )

    users = result.data or []
    if not users:
        logger.info("[RETENTION] No users pending hard deletion")
        return 0

    from gdpr import hard_delete_user
    bucket = os.environ.get("S3_BUCKET_NAME")

    for user in users:
        logger.info(f"[RETENTION] Hard-deleting user {user['email']} (requested >30 days ago)")
        hard_delete_user(db, user["id"], s3_client=bucket)

    logger.info(f"[RETENTION] Hard-deleted {len(users)} users")
    return len(users)


def purge_old_audit_logs(db, days: int = 365):
    """Clean up audit log entries older than N days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    result = (
        db.client.table("audit_log")
        .delete()
        .lt("created_at", cutoff)
        .execute()
    )
    count = len(result.data) if result.data else 0
    logger.info(f"[RETENTION] Purged {count} audit log entries older than {days} days")
    return count


def main():
    """Run all data retention tasks."""
    logger.info("=" * 50)
    logger.info("DATA RETENTION — %s", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    logger.info("=" * 50)

    from db_client import SupabaseClient

    try:
        db = SupabaseClient.from_env()
    except RuntimeError as e:
        logger.error(f"Cannot connect to DB: {e}")
        sys.exit(1)

    purge_old_jobs(db, days=90)

    bucket = os.environ.get("S3_BUCKET_NAME")
    if bucket:
        purge_old_s3_artifacts(bucket, days=30)
    else:
        logger.info("[RETENTION] S3 cleanup skipped (S3_BUCKET_NAME not set)")

    hard_delete_expired_users(db, grace_days=30)
    purge_old_audit_logs(db, days=365)

    logger.info("DATA RETENTION COMPLETE")


if __name__ == "__main__":
    main()
