#!/usr/bin/env python3
"""Batch rescore jobs with rate-limit awareness.

Processes jobs in small batches with delays between them. Safe to interrupt
and resume — tracks which jobs have been rescored via score_version=2.

Usage:
  python scripts/rescore_batch.py --batch-size 5 --delay 60     # 5 jobs per batch, 60s delay
  python scripts/rescore_batch.py --batch-size 5 --delay 60 --max 50  # stop after 50 jobs
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
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
sys.path.insert(0, str(Path(__file__).parent.parent / "lambdas" / "pipeline"))
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

from db_client import SupabaseClient


def score_to_tier(score: float | None) -> str:
    """Map match_score to tier (S/A/B/C/D)."""
    if score is None:
        return "D"
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def main(batch_size: int, delay: int, max_jobs: int | None):
    db = SupabaseClient.from_env()
    user_id = os.environ.get("SUPABASE_USER_ID", "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39")

    # Fetch resume
    resume_r = db.client.table("user_resumes").select("tex_content").eq(
        "user_id", user_id
    ).order("created_at", desc=True).limit(1).execute()
    if not resume_r.data:
        print("ERROR: no resume found"); return
    base_resume = resume_r.data[0]["tex_content"]

    # Find jobs needing rescoring (score_version != 2 or null)
    query = db.client.table("jobs").select("*").eq("user_id", user_id).neq(
        "score_version", 2
    ).order("match_score", desc=True)
    if max_jobs:
        query = query.limit(max_jobs)
    jobs = query.execute().data

    print(f"Jobs needing rescore: {len(jobs)}")
    if not jobs:
        print("All jobs already at score_version=2. Nothing to do.")
        return

    # Import scoring after env is set
    from score_batch import score_single_job_deterministic  # noqa: E402

    processed = 0
    failed = 0
    for i, job in enumerate(jobs, 1):
        try:
            job_input = {
                "title": job["title"],
                "company": job["company"],
                "description": job.get("description", ""),
                "location": job.get("location", ""),
            }
            scores = score_single_job_deterministic(job_input, base_resume, num_calls=3)
            if not scores:
                print(f"[{i}/{len(jobs)}] FAILED: {job['title'][:40]} — no AI providers available")
                failed += 1
                # Stop if we keep failing (likely rate limited)
                if failed >= 3:
                    print(f"\n⚠️  3 consecutive failures — AI providers exhausted. Try again later.")
                    break
                continue

            failed = 0  # reset on success
            tier = score_to_tier(scores["match_score"])
            update = {
                "base_ats_score": scores["ats_score"],
                "base_hm_score": scores["hiring_manager_score"],
                "base_tr_score": scores["tech_recruiter_score"],
                "match_score": scores["match_score"],
                "score_version": 2,
                "score_status": "scored",
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }
            db.client.table("jobs").update(update).eq("job_id", job["job_id"]).execute()
            delta = scores["match_score"] - (job.get("match_score") or 0)
            sign = "+" if delta >= 0 else ""
            print(f"[{i}/{len(jobs)}] {job['title'][:35]} | {job.get('match_score')}→{scores['match_score']} ({sign}{delta:.1f}) | tier={tier}")
            processed += 1

            # Delay between jobs in batch
            if i % batch_size == 0 and i < len(jobs):
                print(f"  --- batch complete, sleeping {delay}s ---")
                time.sleep(delay)
        except Exception as e:
            print(f"[{i}/{len(jobs)}] ERROR: {e}")
            failed += 1

    print(f"\nProcessed: {processed}, failed: {failed}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--delay", type=int, default=60)
    p.add_argument("--max", type=int, default=None, dest="max_jobs")
    args = p.parse_args()
    main(args.batch_size, args.delay, args.max_jobs)
