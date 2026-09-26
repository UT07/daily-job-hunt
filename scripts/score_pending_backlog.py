#!/usr/bin/env python3
"""Clear the score_status='pending' backlog for active (is_expired=false) jobs.

BACKGROUND — read this before changing the "obvious" design of "call the AI
scorer for every pending row". Live-data inspection on 2026-09-25 found that
ALL 1,137 score_status='pending' rows (100%, including all 64 currently
active ones) already carry a real, non-null, non-zero match_score/ats_score/
score_tier. score_batch.py's insert path already scores every job inline
before inserting it (`score_result = score_single_job_deterministic(...)`
runs before `db.table("jobs").insert(job_record)`), but job_record never
sets `score_status` — it relies on the `jobs` table's DEFAULT (`pending`) and
nothing downstream (post_score.py included) ever flips it to `scored`. The
two existing scripts/rescore_*.py scripts are the only code in this repo
that ever writes score_status='scored', and they only run against jobs a
human explicitly re-scores.

So most of this "backlog" is not a throughput problem at all — it's jobs
that were already scored correctly but are invisible to any query that
filters on score_status='scored' (almost certainly why the dashboard shows
nothing: see the throughput fix report). This script therefore has two
paths, chosen per row:

  1. STATUS-FIX (the common case today, zero AI cost): match_score is
     already populated -> just set score_status='scored' + scored_at=now().
  2. REAL SCORE (the path this file's rate-limiting/circuit-breaker logic
     exists for, currently 0 rows but kept correct for whenever a future
     insert path or partial failure leaves a row with no score at all):
     match_score is null/0 -> make one real ai_complete_cached call via
     score_batch.score_single_job_deterministic, exactly like the
     production insert path, including should_skip_scoring's pre-check and
     apply_geo_score_cap's post-hoc cap (so this script can't silently
     resurrect the work-auth scoring bug scripts/backfill_geo_score_cap.py
     already fixed once).

Only path 2 sleeps between jobs or trips the consecutive-failure circuit
breaker — path 1 is a plain DB write, not an AI call, and pacing it against
Groq's token budget would just make clearing a self-inflicted bookkeeping
bug take unnecessarily long.

Resumable by construction, not by a checkpoint file: every successful write
(either path) flips score_status to 'scored', which removes that row from
the WHERE clause this script queries on the very next run — kill it anytime
and re-run with the same flags to continue.

Usage:
    source .venv/bin/activate
    python scripts/score_pending_backlog.py --dry-run              # preview only, no writes, no AI calls
    python scripts/score_pending_backlog.py --max 5                # process at most 5 jobs, then stop
    python scripts/score_pending_backlog.py                        # process the WHOLE backlog -- do not run
                                                                     # without explicit sign-off; see the
                                                                     # throughput fix report before doing this
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Load .env the same way every other scripts/*.py in this repo does.
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

from db_client import SupabaseClient  # noqa: E402
from score_batch import score_single_job_deterministic, score_to_tier, should_skip_scoring  # noqa: E402
from shared.work_auth import apply_geo_score_cap  # noqa: E402

# PostgREST caps a single response at 1000 rows -- paginate well under that
# regardless of how large the backlog grows.
PAGE_SIZE = 500

DEFAULT_USER_ID = os.environ.get("SUPABASE_USER_ID", "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39")

# Groq free tier: 8,000 tokens/minute measured against the production key
# (see score_batch.py's MAX_DESCRIPTION_CHARS/SCORE_MAX_TOKENS comment) ~=
# 1.4 scoring calls/minute ~= one call per ~43s. 45s leaves a small margin.
# Only applied on the REAL-SCORE path -- see module docstring.
DEFAULT_DELAY_SECONDS = 45
DEFAULT_BATCH_SIZE = 5
DEFAULT_FAIL_LIMIT = 3


def _needs_real_scoring(job: dict) -> bool:
    score = job.get("match_score")
    return score is None or score == 0


def _fetch_pending_page(db, user_id: str, offset: int, page_size: int) -> list[dict]:
    q = db.table("jobs").select("*").eq("score_status", "pending").eq("is_expired", False)
    if user_id != "all":
        q = q.eq("user_id", user_id)
    return q.order("first_seen").range(offset, offset + page_size - 1).execute().data or []


def count_pending(db, user_id: str) -> int:
    q = db.table("jobs").select("job_id", count="exact").eq("score_status", "pending").eq("is_expired", False)
    if user_id != "all":
        q = q.eq("user_id", user_id)
    return q.limit(1).execute().count


def score_one_job(db, job: dict, resume_cache: dict, work_auth_cache: dict, dry_run: bool) -> dict:
    """Process a single pending job. Returns a result dict:
        {"outcome": "status_fixed"|"scored"|"failed"|"skipped_precheck"|"skipped_no_resume",
         "match_score": float|None, "score_tier": str|None, "detail": str}
    Never raises for expected failure modes (no providers, no resume) — the
    caller drives pacing/circuit-breaking off "outcome", not exceptions.
    """
    title = job.get("title") or ""
    company = job.get("company") or ""
    user_id = job["user_id"]

    if not _needs_real_scoring(job):
        if not dry_run:
            db.table("jobs").update({
                "score_status": "scored",
                "scored_at": datetime.now(timezone.utc).isoformat(),
            }).eq("job_id", job["job_id"]).execute()
        return {
            "outcome": "status_fixed", "match_score": job.get("match_score"),
            "score_tier": job.get("score_tier"),
            "detail": f"already had a real score (match_score={job.get('match_score')}), only score_status was stale",
        }

    job_input = {"title": title, "company": company, "description": job.get("description", ""), "location": job.get("location", "")}
    skip_reason = should_skip_scoring(job_input)
    if skip_reason:
        return {"outcome": "skipped_precheck", "match_score": None, "score_tier": None, "detail": skip_reason}

    if dry_run:
        return {"outcome": "would_score", "match_score": None, "score_tier": None, "detail": "dry-run, no AI call made"}

    if user_id not in resume_cache:
        r = db.table("user_resumes").select("tex_content").eq("user_id", user_id) \
            .order("created_at", desc=True).limit(1).execute()
        resume_cache[user_id] = r.data[0]["tex_content"] if r.data else None
    resume_tex = resume_cache[user_id]
    if not resume_tex:
        return {"outcome": "skipped_no_resume", "match_score": None, "score_tier": None, "detail": f"no resume for user {user_id}"}

    if user_id not in work_auth_cache:
        try:
            row = db.table("users").select("work_authorizations").eq("id", user_id).single().execute().data or {}
        except Exception:
            row = {}
        work_auth_cache[user_id] = row.get("work_authorizations") or {}

    # num_calls=1, matching score_batch.py's own production call (not the
    # 3-call median rescore_batch.py/rescore_sample.py use) -- this path
    # exists to clear a backlog within Groq's real budget, not to spend 3x
    # the quota per job for a confidence margin nothing here asked for.
    scores = score_single_job_deterministic(job_input, resume_tex, num_calls=1)
    if not scores:
        return {"outcome": "failed", "match_score": None, "score_tier": None, "detail": "all AI providers failed (likely rate-limited)"}

    scores = apply_geo_score_cap(scores, job, work_auth_cache[user_id])
    match_score = scores.get("match_score", 0)
    tier = score_to_tier(match_score)
    update = {
        "match_score": match_score,
        "score_tier": tier,
        "score_status": "scored",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "ats_score": scores.get("ats_score", 0),
        "hiring_manager_score": scores.get("hiring_manager_score", 0),
        "tech_recruiter_score": scores.get("tech_recruiter_score", 0),
        "key_matches": scores.get("key_matches", []),
        "gaps": scores.get("gaps", []),
        "match_reasoning": scores.get("reasoning", ""),
        "archetype": scores.get("archetype", ""),
        "seniority": scores.get("seniority", ""),
        "remote": scores.get("remote", ""),
        "requirement_map": scores.get("requirement_map", []),
        "tailoring_model": f"{scores.get('provider', 'council')}:{scores.get('model', 'consensus')}",
    }
    db.table("jobs").update(update).eq("job_id", job["job_id"]).execute()
    return {"outcome": "scored", "match_score": match_score, "score_tier": tier, "detail": f"provider={scores.get('provider')}"}


def main(max_jobs, delay, batch_size, fail_limit, user_id, dry_run):
    db = SupabaseClient.from_env().client
    total_backlog = count_pending(db, user_id)
    scope = "" if user_id == "all" else f" for user {user_id}"
    print(f"Backlog: {total_backlog} jobs at is_expired=false AND score_status=pending{scope}")
    if total_backlog == 0:
        print("Nothing to do.")
        return
    if max_jobs is not None:
        print(f"This run processes at most {max_jobs} of them.")
    if dry_run:
        print("DRY RUN — no writes, no AI calls.\n")

    resume_cache: dict = {}
    work_auth_cache: dict = {}
    counts = {"status_fixed": 0, "scored": 0, "failed": 0, "skipped_precheck": 0, "skipped_no_resume": 0, "would_score": 0}
    processed = 0
    consecutive_failures = 0
    offset = 0
    stopped_early = False

    while max_jobs is None or processed < max_jobs:
        page = _fetch_pending_page(db, user_id, offset, PAGE_SIZE)
        if not page:
            break
        for job in page:
            if max_jobs is not None and processed >= max_jobs:
                break
            result = score_one_job(db, job, resume_cache, work_auth_cache, dry_run)
            counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
            processed += 1
            title = (job.get("title") or "")[:45]
            label = {
                "status_fixed": "STATUS-FIX", "scored": "SCORED", "failed": "FAILED",
                "skipped_precheck": "SKIP", "skipped_no_resume": "SKIP", "would_score": "WOULD-SCORE",
            }[result["outcome"]]
            extra = f" match_score={result['match_score']} tier={result['score_tier']}" if result["match_score"] is not None else ""
            print(f"[{processed}/{min(total_backlog, max_jobs) if max_jobs else total_backlog}] {label} {title!r} @ {job.get('company')}{extra} — {result['detail']}")

            if result["outcome"] == "failed":
                consecutive_failures += 1
                if consecutive_failures >= fail_limit:
                    print(f"\n{consecutive_failures} consecutive AI failures — providers likely rate-limited/exhausted. "
                          f"Stopping early; re-run later with the same flags to resume.")
                    stopped_early = True
                    break
            elif result["outcome"] == "scored":
                consecutive_failures = 0

            # Only the real-scoring path spends AI-provider budget; only it
            # needs to respect Groq's rate limit.
            if result["outcome"] in ("scored", "failed"):
                if processed % batch_size == 0:
                    print(f"  --- {batch_size}-job batch boundary, pausing {delay}s ---")
                time.sleep(delay)
        if stopped_early:
            break
        offset += PAGE_SIZE

    remaining = count_pending(db, user_id)
    print(f"\nProcessed {processed}: status_fixed={counts['status_fixed']}, scored={counts['scored']}, "
          f"failed={counts['failed']}, skipped_precheck={counts['skipped_precheck']}, "
          f"skipped_no_resume={counts['skipped_no_resume']}, would_score={counts['would_score']}")
    print(f"Remaining backlog: {remaining}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max", type=int, default=None, dest="max_jobs", help="stop after this many jobs (default: whole backlog)")
    p.add_argument("--delay", type=int, default=DEFAULT_DELAY_SECONDS, help="seconds between real AI-scoring calls (default 45)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="print a batch-boundary pause every N real-scored jobs")
    p.add_argument("--fail-limit", type=int, default=DEFAULT_FAIL_LIMIT, help="stop after this many consecutive AI failures")
    p.add_argument("--user-id", type=str, default=DEFAULT_USER_ID, help="restrict to one user_id, or 'all'")
    p.add_argument("--dry-run", action="store_true", help="preview only — no writes, no AI calls")
    args = p.parse_args()
    main(args.max_jobs, args.delay, args.batch_size, args.fail_limit, args.user_id, args.dry_run)
