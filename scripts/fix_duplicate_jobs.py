#!/usr/bin/env python3
"""One-time cleanup: remove cross-query duplicate jobs from jobs and jobs_raw.

The same job scraped via different search queries (e.g. "SRE" and "DevOps")
gets a different job_hash but identical company+title. This creates 110
duplicate company+title pairs (234 rows) in the database.

For each duplicate group this script:
  1. Keeps the row with the best data (highest score, most complete artifacts,
     longest description).
  2. Deletes the loser rows from both `jobs` and `jobs_raw`.

Usage:
  python scripts/fix_duplicate_jobs.py --dry-run   # inspect without changes
  python scripts/fix_duplicate_jobs.py             # apply deletions
"""
import argparse
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env and add project root to sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

from db_client import SupabaseClient  # noqa: E402

# ---------------------------------------------------------------------------
# Normalisation helpers (mirror utils/canonical_hash.py without importing
# from lambdas/ path, keeping this script self-contained)
# ---------------------------------------------------------------------------
_LEGAL_SUFFIXES = re.compile(
    r"\s+(?:Inc\.?|Ltd\.?|LLC|GmbH|Corp\.?|Co\.?|PLC|LP|LLP|SA|AG|BV|NV|SE)\s*$",
    re.IGNORECASE,
)


def _normalize_company(company: str) -> str:
    name = (company or "").strip().lower()
    name = _LEGAL_SUFFIXES.sub("", name)
    return name.strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip().lower()


def _dedup_key(job: dict) -> str:
    return f"{_normalize_company(job.get('company', ''))}|{_normalize_title(job.get('title', ''))}"


# ---------------------------------------------------------------------------
# Richness scoring — higher tuple = better row to keep
# ---------------------------------------------------------------------------
def _richness_score(job: dict) -> tuple:
    """Return a comparable tuple: higher = richer / better row to keep."""
    # 1. Prefer rows that have been scored
    scored = 1 if job.get("match_score") else 0
    match_score = job.get("match_score") or 0
    # 2. Prefer rows that have artifacts
    has_resume = 1 if job.get("resume_s3_url") else 0
    has_cover = 1 if job.get("cover_letter_s3_url") else 0
    has_contacts = 1 if job.get("contacts") else 0
    artifact_count = has_resume + has_cover + has_contacts
    # 3. Prefer longer description
    desc_len = len(job.get("description", "") or "")
    # 4. Prefer more populated fields
    field_count = sum(1 for v in job.values() if v is not None and v != "")
    # 5. Prefer most recent scored_at as final tiebreaker
    scored_at = job.get("scored_at") or ""
    return (scored, match_score, artifact_count, desc_len, field_count, scored_at)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(dry_run: bool) -> None:
    db = SupabaseClient.from_env()

    print("=" * 64)
    print("NaukriBaba — Cross-Query Duplicate Job Cleanup")
    print(f"  Dry run: {dry_run}")
    print("=" * 64)

    # ------------------------------------------------------------------
    # 1. Fetch all jobs (select only columns we need for scoring + IDs)
    # ------------------------------------------------------------------
    print("\nFetching jobs table...")
    all_jobs = (
        db.client.table("jobs")
        .select(
            "job_id, job_hash, user_id, title, company, description, "
            "match_score, resume_s3_url, cover_letter_s3_url, contacts, "
            "scored_at"
        )
        .execute()
        .data
    )
    print(f"  Total rows: {len(all_jobs)}")

    # ------------------------------------------------------------------
    # 2. Group by (user_id, dedup_key)
    # ------------------------------------------------------------------
    groups: dict[str, list[dict]] = {}
    for job in all_jobs:
        key = f"{job.get('user_id', '')}|{_dedup_key(job)}"
        groups.setdefault(key, []).append(job)

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    total_to_delete = sum(len(v) - 1 for v in duplicate_groups.values())

    print(f"\n  Duplicate company+title groups: {len(duplicate_groups)}")
    print(f"  Rows to delete:                 {total_to_delete}")

    if not duplicate_groups:
        print("\nNo duplicates found — nothing to do.")
        return

    # ------------------------------------------------------------------
    # 3. For each group, decide keeper vs losers
    # ------------------------------------------------------------------
    delete_job_ids: list[str] = []
    delete_job_hashes: list[str] = []

    print("\nDuplicate groups:")
    for key, jobs in sorted(duplicate_groups.items()):
        ranked = sorted(jobs, key=_richness_score, reverse=True)
        keeper = ranked[0]
        losers = ranked[1:]

        print(
            f"\n  [{keeper.get('company', '?')}] {keeper.get('title', '?')}"
        )
        print(
            f"    KEEP  job_id={keeper['job_id'][:8]}... "
            f"score={keeper.get('match_score')} "
            f"resume={'Y' if keeper.get('resume_s3_url') else 'N'} "
            f"desc={len(keeper.get('description') or '')}c"
        )
        for loser in losers:
            print(
                f"    DROP  job_id={loser['job_id'][:8]}... "
                f"score={loser.get('match_score')} "
                f"resume={'Y' if loser.get('resume_s3_url') else 'N'} "
                f"desc={len(loser.get('description') or '')}c"
            )
            delete_job_ids.append(loser["job_id"])
            if loser.get("job_hash"):
                delete_job_hashes.append(loser["job_hash"])

    print(f"\nTotal rows to delete from jobs:     {len(delete_job_ids)}")
    print(f"Matching rows to delete from jobs_raw: {len(delete_job_hashes)}")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return

    # ------------------------------------------------------------------
    # 4. Delete from jobs
    # ------------------------------------------------------------------
    print("\nDeleting from jobs table...")
    jobs_deleted = 0
    jobs_errors = 0
    for job_id in delete_job_ids:
        try:
            db.client.table("jobs").delete().eq("job_id", job_id).execute()
            jobs_deleted += 1
        except Exception as exc:
            jobs_errors += 1
            print(f"  ERROR deleting jobs.job_id={job_id}: {exc}")

    print(f"  Deleted {jobs_deleted} rows ({jobs_errors} errors)")

    # ------------------------------------------------------------------
    # 5. Delete matching rows from jobs_raw
    # ------------------------------------------------------------------
    print("\nDeleting from jobs_raw table...")
    raw_deleted = 0
    raw_errors = 0
    for job_hash in delete_job_hashes:
        try:
            db.client.table("jobs_raw").delete().eq("job_hash", job_hash).execute()
            raw_deleted += 1
        except Exception as exc:
            raw_errors += 1
            print(f"  ERROR deleting jobs_raw.job_hash={job_hash}: {exc}")

    print(f"  Deleted {raw_deleted} rows ({raw_errors} errors)")

    print("\nDone.")
    print(f"  jobs deleted:     {jobs_deleted}")
    print(f"  jobs_raw deleted: {raw_deleted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Remove cross-query duplicate jobs from the database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making any changes",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
