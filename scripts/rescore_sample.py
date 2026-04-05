#!/usr/bin/env python3
"""Rescore a sample of jobs with the new deterministic pipeline.

Tests: canonical_hash, score_single_job_deterministic (3-call median),
compute_base_scores, compute_tailored_scores, writing_quality_score.

Usage:
  python scripts/rescore_sample.py --count 5         # rescore 5 jobs
  python scripts/rescore_sample.py --count 5 --dry-run  # show plan only
"""
import argparse
import json
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


def main(count: int, dry_run: bool):
    db = SupabaseClient.from_env()
    print("=" * 60)
    print(f"Rescore sample — {count} jobs, dry_run={dry_run}")
    print("=" * 60)

    # Get user_id from env or use default
    user_id = os.environ.get("SUPABASE_USER_ID", "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39")
    print(f"User: {user_id}")

    # Fetch user's resume
    resume_r = db.client.table("user_resumes").select("tex_content").eq(
        "user_id", user_id
    ).order("created_at", desc=True).limit(1).execute()
    if not resume_r.data or not resume_r.data[0].get("tex_content"):
        print("ERROR: no resume found")
        return
    base_resume = resume_r.data[0]["tex_content"]
    print(f"Base resume: {len(base_resume)} chars")

    # Fetch top N jobs (by match_score)
    jobs_r = db.client.table("jobs").select("*").eq("user_id", user_id).order(
        "match_score", desc=True
    ).limit(count).execute()
    jobs = jobs_r.data
    print(f"Jobs to rescore: {len(jobs)}")
    print()

    if dry_run:
        print("Jobs that would be rescored:")
        for j in jobs:
            print(f"  - {j['title'][:40]} @ {j['company']} (current score={j.get('match_score')})")
        return

    # Import the new scoring functions
    sys.path.insert(0, str(Path(__file__).parent.parent / "lambdas" / "pipeline"))
    # We need to set AWS_DEFAULT_REGION for boto3 in merge_dedup
    os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

    from score_batch import score_single_job_deterministic, score_writing_quality

    results = []
    for i, job in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}] {job['title'][:40]} @ {job['company']}")
        print(f"  old match_score: {job.get('match_score')}")

        # Build job dict for scoring
        job_input = {
            "title": job["title"],
            "company": job["company"],
            "description": job.get("description", ""),
            "location": job.get("location", ""),
        }

        # Multi-call median scoring (3 calls, take median)
        base_scores = score_single_job_deterministic(job_input, base_resume, num_calls=3)
        if not base_scores:
            print("  FAILED: no scoring providers available")
            continue

        print(f"  new base scores (temp=0, 3-call median):")
        print(f"    ats={base_scores['ats_score']}, hm={base_scores['hiring_manager_score']}, tr={base_scores['tech_recruiter_score']}, match={base_scores['match_score']}")

        # Update the job in Supabase with base scores
        update = {
            "base_ats_score": base_scores["ats_score"],
            "base_hm_score": base_scores["hiring_manager_score"],
            "base_tr_score": base_scores["tech_recruiter_score"],
            "match_score": base_scores["match_score"],
            "score_version": 2,
            "score_status": "scored",
            "scored_at": "now()",
        }

        # Skip scored_at Supabase-side default, use iso format
        from datetime import datetime, timezone
        update["scored_at"] = datetime.now(timezone.utc).isoformat()

        db.client.table("jobs").update(update).eq("job_id", job["job_id"]).execute()
        results.append({"job_id": job["job_id"], "title": job["title"], "old_score": job.get("match_score"), "new_score": base_scores["match_score"]})
        print(f"  updated")
        print()

    # Summary
    print("=" * 60)
    print("Summary:")
    for r in results:
        delta = (r["new_score"] or 0) - (r["old_score"] or 0)
        sign = "+" if delta >= 0 else ""
        print(f"  {r['title'][:40]}: {r['old_score']} → {r['new_score']} ({sign}{delta:.1f})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(args.count, args.dry_run)
