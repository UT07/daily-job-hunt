#!/usr/bin/env python3
"""One-time: deduplicate jobs that share a canonical_hash.

Keeps the richest version per hash group:
  1. Longest description
  2. Most populated fields
  3. Highest match_score (prefer the more generous score)
  4. Most recent scored_at

Deletes the others. Dry-run by default.

Usage:
  python scripts/dedup_canonical_hashes.py --dry-run
  python scripts/dedup_canonical_hashes.py            # actually dedupe
"""
import argparse
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sys.path.insert(0, str(Path(__file__).parent.parent))
from db_client import SupabaseClient


def _richness_score(job: dict) -> tuple:
    """Higher tuple = richer."""
    desc_len = len(job.get("description", "") or "")
    field_count = sum(1 for v in job.values() if v is not None and v != "")
    match_score = job.get("match_score") or 0
    scored_at = job.get("scored_at") or ""
    return (desc_len, field_count, match_score, scored_at)


def main(dry_run: bool):
    db = SupabaseClient.from_env()

    print("=" * 60)
    print("NaukriBaba — Canonical Hash Deduplication")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    # Fetch all jobs
    all_jobs = db.client.table("jobs").select("*").execute().data
    print(f"Fetched {len(all_jobs)} jobs")

    # Group by canonical_hash
    groups: dict[str, list[dict]] = {}
    for j in all_jobs:
        h = j.get("canonical_hash")
        if not h:
            continue
        groups.setdefault(h, []).append(j)

    duplicates = {h: jobs for h, jobs in groups.items() if len(jobs) > 1}
    print(f"Duplicate groups: {len(duplicates)}")
    print(f"Total rows to delete: {sum(len(jobs) - 1 for jobs in duplicates.values())}")
    print()

    delete_ids = []
    for h, jobs in duplicates.items():
        jobs_sorted = sorted(jobs, key=_richness_score, reverse=True)
        keeper = jobs_sorted[0]
        losers = jobs_sorted[1:]
        print(f"Hash {h}:")
        print(f"  KEEP: {keeper['title'][:40]} @ {keeper['company']} (score={keeper.get('match_score')}, desc={len(keeper.get('description') or '')} chars)")
        for loser in losers:
            print(f"  DROP: {loser['title'][:40]} @ {loser['company']} (score={loser.get('match_score')}, desc={len(loser.get('description') or '')} chars)")
            delete_ids.append(loser["job_id"])

    print()
    if dry_run:
        print(f"[DRY RUN] Would delete {len(delete_ids)} rows — skipping")
        return

    # Delete loser rows
    deleted = 0
    errors = 0
    for row_id in delete_ids:
        try:
            db.client.table("jobs").delete().eq("job_id", row_id).execute()
            deleted += 1
        except Exception as e:
            errors += 1
            print(f"  ERROR deleting {row_id}: {e}")

    print(f"Deleted {deleted} rows ({errors} errors)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(args.dry_run)
