#!/usr/bin/env python3
"""Backfill locally-processed jobs to Supabase dashboard.

Reads raw_jobs.json from output directories, filters for scored jobs,
and upserts them to the Supabase jobs table.

Usage:
    python scripts/backfill_jobs.py                    # Backfill scored jobs (match_score > 0)
    python scripts/backfill_jobs.py --all              # Backfill ALL jobs (including unscored)
    python scripts/backfill_jobs.py --min-score 60     # Only jobs scoring 60+
    python scripts/backfill_jobs.py --dry-run           # Show what would be done
    python scripts/backfill_jobs.py --date 2026-04-02   # Only backfill a specific date

Requires:
    SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_USER_ID in .env or environment.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env manually (same approach as backfill_all.py for consistency)
env_path = ROOT / ".env"
if env_path.exists():
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if key.strip() and val:
                os.environ.setdefault(key.strip(), val)

from db_client import SupabaseClient


def load_local_jobs(output_dir: str, date_filter: str | None = None) -> list[dict]:
    """Load jobs from raw_jobs.json files in output directories.

    Args:
        output_dir: Path to the output directory containing date subdirectories.
        date_filter: If set, only load jobs from this specific date (YYYY-MM-DD).

    Returns:
        List of job dicts from all matching raw_jobs.json files.
    """
    jobs = []
    output_path = Path(output_dir)

    if not output_path.exists():
        print(f"  Output directory not found: {output_path}")
        return jobs

    for date_dir in sorted(output_path.iterdir()):
        if not date_dir.is_dir() or date_dir.name.startswith("."):
            continue
        if date_filter and date_dir.name != date_filter:
            continue

        raw_path = date_dir / "raw_jobs.json"
        if raw_path.exists():
            with open(raw_path) as f:
                day_jobs = json.load(f)
                print(f"  {date_dir.name}: {len(day_jobs)} jobs loaded")
                jobs.extend(day_jobs)

    return jobs


def load_seen_jobs(output_dir: str) -> dict:
    """Load seen_jobs.json for first_seen/last_seen enrichment.

    Returns:
        Dict mapping job_id -> {first_seen, last_seen, score, ...}
    """
    seen_path = Path(output_dir) / "seen_jobs.json"
    if seen_path.exists():
        with open(seen_path) as f:
            return json.load(f)
    return {}


def map_job_to_row(job: dict, user_id: str, seen_data: dict) -> dict:
    """Map a raw job dict to a Supabase jobs table row.

    Args:
        job: Raw job dict from raw_jobs.json (matches Job.to_dict() format).
        user_id: The Supabase user ID to associate with.
        seen_data: Dict from seen_jobs.json for first_seen/last_seen enrichment.

    Returns:
        Dict ready for Supabase upsert.
    """
    job_id = job.get("job_id") or job.get("id", "")
    today = date.today().isoformat()

    # Enrich with seen_jobs data for first_seen/last_seen
    seen = seen_data.get(job_id, {})
    first_seen = seen.get("first_seen") or job.get("scraped_at", today)[:10]
    last_seen = seen.get("last_seen") or today

    row = {
        "job_id": job_id,
        "user_id": user_id,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "description": job.get("description", ""),
        "apply_url": job.get("apply_url", ""),
        "source": job.get("source", ""),
        "match_score": job.get("match_score", 0),
        "ats_score": job.get("ats_score", 0),
        "hiring_manager_score": job.get("hiring_manager_score", 0),
        "tech_recruiter_score": job.get("tech_recruiter_score", 0),
        "resume_s3_url": job.get("resume_s3_url") or None,
        "cover_letter_s3_url": job.get("cover_letter_s3_url") or None,
        "application_status": job.get("application_status", "New"),
        "first_seen": first_seen,
        "last_seen": last_seen,
    }
    return row


def backfill(
    user_id: str,
    output_dir: str = "output",
    min_score: float = 0,
    include_all: bool = False,
    date_filter: str | None = None,
    dry_run: bool = False,
):
    """Push locally-scored jobs to Supabase.

    Args:
        user_id: Supabase user UUID.
        output_dir: Path to the output directory.
        min_score: Only backfill jobs with match_score >= this value.
        include_all: If True, include all jobs regardless of score.
        date_filter: If set, only process this specific date directory.
        dry_run: If True, report what would be done without writing.
    """
    db = SupabaseClient.from_env()

    print(f"Loading local jobs from {output_dir}/...")
    all_jobs = load_local_jobs(output_dir, date_filter)
    print(f"Total local jobs: {len(all_jobs)}")

    if not all_jobs:
        print("No jobs found. Check that output/YYYY-MM-DD/raw_jobs.json exists.")
        return

    # Filter for jobs with scores (unless --all is set)
    if include_all:
        scored = all_jobs
        print(f"Including ALL {len(scored)} jobs (--all flag)")
    else:
        # Default: only jobs with match_score > 0; with --min-score N: jobs >= N
        if min_score > 0:
            scored = [j for j in all_jobs if j.get("match_score", 0) >= min_score]
            print(f"Jobs with match_score >= {min_score}: {len(scored)}")
        else:
            scored = [j for j in all_jobs if j.get("match_score", 0) > 0]
            print(f"Jobs with match_score > 0: {len(scored)}")

    if not scored:
        print("No jobs pass the score filter.")
        print("  Hint: use --all to include unscored jobs.")
        return

    # Check what's already in Supabase to report overlap
    existing = (
        db.client.table("jobs")
        .select("job_id")
        .eq("user_id", user_id)
        .execute()
    )
    existing_ids = {r["job_id"] for r in (existing.data or [])}
    print(f"Already in Supabase: {len(existing_ids)}")

    new_jobs = [
        j for j in scored
        if (j.get("job_id") or j.get("id", "")) not in existing_ids
    ]
    update_jobs = [
        j for j in scored
        if (j.get("job_id") or j.get("id", "")) in existing_ids
    ]
    print(f"New jobs to insert: {len(new_jobs)}")
    print(f"Existing jobs to update: {len(update_jobs)}")

    if dry_run:
        print("\n[DRY RUN] Would upsert these jobs:")
        for j in scored[:20]:
            jid = j.get("job_id") or j.get("id", "?")
            status = "UPDATE" if jid in existing_ids else "INSERT"
            print(f"  [{status}] {j.get('title', '?')} @ {j.get('company', '?')} "
                  f"(score={j.get('match_score', 0)}, id={jid})")
        if len(scored) > 20:
            print(f"  ... and {len(scored) - 20} more")
        return

    # Load seen_jobs for date enrichment
    seen_data = load_seen_jobs(output_dir)
    print(f"Seen jobs loaded: {len(seen_data)} entries")

    # Upsert to Supabase
    success = 0
    errors = 0
    for job in scored:
        try:
            row = map_job_to_row(job, user_id, seen_data)
            db.client.table("jobs").upsert(
                row, on_conflict="job_id,user_id"
            ).execute()
            success += 1
        except Exception as e:
            errors += 1
            print(f"  Error: {job.get('title', '?')} @ {job.get('company', '?')}: {e}")

    print(f"\nBackfill complete: {success} upserted, {errors} errors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill locally-processed jobs to Supabase dashboard"
    )
    parser.add_argument(
        "--min-score", type=float, default=0,
        help="Only backfill jobs with match_score >= this value (default: only scored jobs)"
    )
    parser.add_argument(
        "--all", action="store_true", dest="include_all",
        help="Include ALL jobs regardless of score (overrides --min-score)"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Only process a specific date directory (e.g. 2026-04-02)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing to Supabase"
    )
    parser.add_argument(
        "--output-dir", type=str, default=str(ROOT / "output"),
        help="Path to the output directory (default: <project>/output)"
    )
    args = parser.parse_args()

    user_id = os.environ.get("SUPABASE_USER_ID")
    if not user_id:
        print("Error: Set SUPABASE_USER_ID environment variable.")
        print("  Example: export SUPABASE_USER_ID='7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39'")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"NaukriBaba Job Backfill")
    print(f"  User ID:    {user_id}")
    print(f"  Output dir: {args.output_dir}")
    print(f"  Filter:     {'all jobs' if args.include_all else f'match_score > {args.min_score}'}")
    print(f"  Date:       {args.date or 'all'}")
    print(f"  Dry run:    {args.dry_run}")
    print(f"{'=' * 60}")

    backfill(
        user_id=user_id,
        output_dir=args.output_dir,
        min_score=args.min_score,
        include_all=args.include_all,
        date_filter=args.date,
        dry_run=args.dry_run,
    )
