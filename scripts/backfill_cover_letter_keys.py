#!/usr/bin/env python3
"""Recover jobs.cover_letter_s3_key from the presigned URL already stored.

Migration 20260929120000 added cover_letter_s3_key so _refresh_s3_urls could
re-sign cover-letter links before they expire, and stated:

    Backfill is not possible for existing rows — the key was never recorded
    anywhere — so their links stay dead until the job is regenerated.

That is wrong, and the evidence was in the row the whole time. A presigned S3
URL carries the object key in its PATH; only the signature in the query string
expires. So the key can be read straight back out of cover_letter_s3_url.

Measured 2026-09-29 before running this: 673 rows carried a
cover_letter_s3_url, exactly 1 carried a cover_letter_s3_key, and 673 of 673
URLs yielded a parseable key across two historical naming conventions:

    418  users/{uid}/cover_letters/{job_hash}_cover.pdf
    255  users/{uid}/{date}/cover_letters/{Name}_{Title}_{Company}_...pdf

Every candidate key is confirmed with head_object before it is written, so a
row whose object really is gone stays NULL rather than gaining a key that
resolves to nothing — which would be worse than the dead link it replaces.

Additive only: it writes one column, on rows where it is NULL, and never
deletes or overwrites.

    python scripts/backfill_cover_letter_keys.py            # dry run
    python scripts/backfill_cover_letter_keys.py --apply
"""
import argparse
import os
import sys
from collections import Counter
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from db_client import SupabaseClient  # noqa: E402

BUCKET = os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))


def key_from_presigned(url: str, bucket: str = BUCKET) -> str | None:
    """Object key from a presigned URL, for both S3 URL styles.

    virtual-hosted:  https://{bucket}.s3.{region}.amazonaws.com/{key}?X-Amz-...
    path-style:      https://s3.{region}.amazonaws.com/{bucket}/{key}?X-Amz-...

    The query string holds the signature and expiry; the path holds the key and
    does not expire with it.
    """
    if not url:
        return None
    path = unquote(urlparse(url).path).lstrip("/")
    if not path:
        return None
    if path.startswith(f"{bucket}/"):
        path = path[len(bucket) + 1:]
    return path or None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write; otherwise dry run")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    db = SupabaseClient(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    rows = (
        db.client.table("jobs")
        .select("job_id,job_hash,cover_letter_s3_url,cover_letter_s3_key")
        .not_.is_("cover_letter_s3_url", "null")
        .execute()
        .data
    )
    stats = Counter()
    todo = []
    for r in rows:
        if r.get("cover_letter_s3_key"):
            stats["already had a key"] += 1
            continue
        key = key_from_presigned(r["cover_letter_s3_url"])
        if not key:
            stats["no key in URL"] += 1
            continue
        try:
            s3.head_object(Bucket=BUCKET, Key=key)
        except Exception:
            stats["object missing in S3"] += 1
            continue
        stats["recoverable"] += 1
        todo.append((r["job_id"], key))

    print(f"{len(rows)} rows carry a cover_letter_s3_url")
    for k, v in stats.most_common():
        print(f"  {v:>5}  {k}")

    if not args.apply:
        print(f"\nDRY RUN — would set cover_letter_s3_key on {len(todo)} rows. "
              f"Re-run with --apply.")
        for job_id, key in todo[:3]:
            print(f"    {job_id[:8]}  {key}")
        return 0

    written = failed = 0
    for job_id, key in todo:
        try:
            db.client.table("jobs").update({"cover_letter_s3_key": key}) \
                .eq("job_id", job_id).execute()
            written += 1
        except Exception as exc:
            failed += 1
            print(f"  FAILED {job_id}: {exc}")
    print(f"\nwrote {written}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
