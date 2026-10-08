#!/usr/bin/env python3
"""Data retention automation — cleanup job.

Enforces data retention policies:
1. Purge job listings older than 90 days — except jobs the user engaged with
2. Delete S3 objects under users/ older than 30 days — except any object a
   jobs or resume_versions row still references
3. Hard-delete users who requested deletion >30 days ago
4. Clean up old audit log entries (>1 year)

DRY RUN BY DEFAULT. It reports what it would delete and deletes nothing:

    python scripts/data_retention.py            # report only
    python scripts/data_retention.py --commit   # actually delete

Why, 2026-10-08: as written, the first run of this script would have deleted
every job older than 90 days including ones the user APPLIED to (the
lifecycle rule in shared/job_lifecycle.py keeps those forever), and every
users/ object older than 30 days, including the tailored .tex/.pdf that the
dashboard and Studio open for any job first seen more than a month ago. And
there was no way to look before it did. It is not scheduled anywhere today.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.job_lifecycle import ENGAGED_STATUSES, age_days  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname).1s] %(message)s")
logger = logging.getLogger(__name__)

_PAGE = 1000


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def _mode(commit: bool) -> str:
    return "DELETED" if commit else "WOULD DELETE (dry run)"


def _all_rows(db, table: str, columns: str) -> list[dict]:
    """Every row of `table`, paged. Raises on failure: callers must not guess."""
    rows, offset = [], 0
    while True:
        page = (db.client.table(table).select(columns)
                .range(offset, offset + _PAGE - 1).execute()).data or []
        rows += page
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def purge_old_jobs(db, days: int = 90, commit: bool = False, now=None) -> list[str]:
    """Delete jobs older than `days`, never ones the user engaged with.

    Returns the job_ids deleted (or, in a dry run, that would be). Candidates
    are selected and filtered here rather than with a PostgREST NOT IN,
    because `application_status NOT IN (...)` is NULL for a NULL status and
    would silently keep every never-touched job. A row whose first_seen is
    missing or unparseable is never purged (job_lifecycle.age_days -> None).
    """
    cutoff = (_now(now) - timedelta(days=days)).isoformat()
    candidates = (db.client.table("jobs")
                  .select("job_id, first_seen, application_status")
                  .lt("first_seen", cutoff).execute()).data or []
    doomed, exempt = [], 0
    for row in candidates:
        if (row.get("application_status") or "") in ENGAGED_STATUSES:
            exempt += 1
            continue
        age = age_days(row.get("first_seen"), now=_now(now))
        if age is None or age < days:
            continue
        doomed.append(row["job_id"])

    if commit:
        for i in range(0, len(doomed), 100):
            db.client.table("jobs").delete().in_("job_id", doomed[i:i + 100]).execute()
    logger.info(f"[RETENTION] {_mode(commit)} {len(doomed)} jobs older than {days} days "
                f"({exempt} engaged job(s) kept: {', '.join(sorted(ENGAGED_STATUSES))})")
    return doomed


def _key_from_url(url: str | None) -> str | None:
    """The object key inside a (possibly presigned) S3 URL.

    A presigned URL is a key plus a signature; only the signature expires
    (CLAUDE.md #9). A row whose *_s3_key column is empty can still reference
    an object through its URL.
    """
    if not url:
        return None
    path = unquote(urlparse(url).path).lstrip("/")
    # path-style URLs carry the bucket as the first segment
    if path and not path.startswith("users/") and "/users/" in f"/{path}":
        slashed = f"/{path}"
        path = slashed[slashed.index("/users/") + 1:]
    return path or None


def _siblings(key: str) -> set[str]:
    """A .tex and its compiled .pdf live side by side; protect both."""
    out = {key}
    for a, b in ((".pdf", ".tex"), (".tex", ".pdf")):
        if key.endswith(a):
            out.add(key[: -len(a)] + b)
    return out


def referenced_keys(db, exclude_job_ids=frozenset()) -> set[str]:
    """Every S3 key a live row still points at. Raises if it cannot be read."""
    keys: set[str] = set()
    jobs = _all_rows(db, "jobs", "job_id, user_id, job_hash, canonical_hash, resume_s3_key, "
                                 "cover_letter_s3_key, resume_s3_url, cover_letter_s3_url")
    for j in jobs:
        if j.get("job_id") in exclude_job_ids:
            continue
        for col in ("resume_s3_key", "cover_letter_s3_key"):
            if j.get(col):
                keys |= _siblings(j[col])
        for col in ("resume_s3_url", "cover_letter_s3_url"):
            k = _key_from_url(j.get(col))
            if k:
                keys |= _siblings(k)
        # The tex keys the tailoring and cover-letter Lambdas write, which the
        # Studio opens directly by convention rather than through a column.
        uid = j.get("user_id")
        for h in {j.get("job_hash"), j.get("canonical_hash")} - {None, ""}:
            keys |= _siblings(f"users/{uid}/resumes/{h}_tailored.tex")
            keys |= _siblings(f"users/{uid}/cover_letters/{h}_cover.tex")
    for v in _all_rows(db, "resume_versions", "resume_s3_key, cover_letter_s3_key"):
        for col in ("resume_s3_key", "cover_letter_s3_key"):
            if v.get(col):
                keys |= _siblings(v[col])
    return keys


def purge_old_s3_artifacts(bucket_name: str, db, days: int = 30, commit: bool = False,
                           s3=None, now=None, exclude_job_ids=frozenset()) -> int:
    """Delete users/ objects older than `days` that no row references.

    Builds the referenced set FIRST and lets any failure propagate: deleting
    without knowing what is in use is the failure this guards against.
    `exclude_job_ids` are jobs this run is purging, whose artifacts are
    therefore no longer protected (so a dry run reports what --commit does).
    """
    protected = referenced_keys(db, exclude_job_ids=exclude_job_ids)
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    cutoff = _now(now) - timedelta(days=days)

    doomed, kept = [], 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket_name, Prefix="users/"):
        for obj in page.get("Contents", []):
            modified = obj["LastModified"]
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            if modified >= cutoff:
                continue
            if obj["Key"] in protected:
                kept += 1
                continue
            doomed.append(obj["Key"])

    if commit:
        for i in range(0, len(doomed), 1000):
            s3.delete_objects(Bucket=bucket_name,
                              Delete={"Objects": [{"Key": k} for k in doomed[i:i + 1000]]})
    logger.info(f"[RETENTION] {_mode(commit)} {len(doomed)} S3 objects older than {days} days "
                f"({kept} old object(s) kept because a row references them)")
    return len(doomed)


def hard_delete_expired_users(db, grace_days: int = 30, commit: bool = False, now=None):
    """Hard-delete users whose deletion was requested >grace_days ago."""
    cutoff = (_now(now) - timedelta(days=grace_days)).isoformat()

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
    if not commit:
        logger.info(f"[RETENTION] {_mode(False)} {len(users)} user(s) past the deletion grace period")
        return len(users)

    from gdpr import hard_delete_user
    bucket = os.environ.get("S3_BUCKET_NAME")

    for user in users:
        logger.info(f"[RETENTION] Hard-deleting user {user['email']} (requested >{grace_days} days ago)")
        hard_delete_user(db, user["id"], s3_client=bucket)

    logger.info(f"[RETENTION] Hard-deleted {len(users)} users")
    return len(users)


def purge_old_audit_logs(db, days: int = 365, commit: bool = False, now=None):
    """Clean up audit log entries older than N days."""
    cutoff = (_now(now) - timedelta(days=days)).isoformat()
    if not commit:
        rows = (db.client.table("audit_log").select("id", count="exact")
                .lt("created_at", cutoff).limit(1).execute())
        count = rows.count or 0
    else:
        result = db.client.table("audit_log").delete().lt("created_at", cutoff).execute()
        count = len(result.data) if result.data else 0
    logger.info(f"[RETENTION] {_mode(commit)} {count} audit log entries older than {days} days")
    return count


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--commit", action="store_true",
                    help="actually delete; without it, report what would be deleted")
    return ap.parse_args(argv)


def main(argv=None):
    """Run all data retention tasks."""
    args = parse_args(argv)
    logger.info("=" * 50)
    logger.info("DATA RETENTION — %s — %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "COMMIT" if args.commit else "DRY RUN (pass --commit to delete)")
    logger.info("=" * 50)

    from db_client import SupabaseClient

    try:
        db = SupabaseClient.from_env()
    except RuntimeError as e:
        logger.error(f"Cannot connect to DB: {e}")
        sys.exit(1)

    purged = purge_old_jobs(db, days=90, commit=args.commit)

    bucket = os.environ.get("S3_BUCKET_NAME")
    if bucket:
        purge_old_s3_artifacts(bucket, db, days=30, commit=args.commit,
                               exclude_job_ids=frozenset(purged))
    else:
        logger.info("[RETENTION] S3 cleanup skipped (S3_BUCKET_NAME not set)")

    hard_delete_expired_users(db, grace_days=30, commit=args.commit)
    purge_old_audit_logs(db, days=365, commit=args.commit)

    logger.info("DATA RETENTION COMPLETE%s", "" if args.commit else " (dry run, nothing deleted)")


if __name__ == "__main__":
    main()
