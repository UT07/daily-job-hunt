#!/usr/bin/env python3
"""Backfill the geo + work-auth score cap on existing job rows.

Pure DB operation — does NOT re-call AI. Reads user.work_authorizations,
re-evaluates each job through `apply_geo_score_cap`, and updates the row
if the cap demotes its scores.

Usage:
    python scripts/backfill_geo_score_cap.py             # dry-run by default
    python scripts/backfill_geo_score_cap.py --commit    # apply updates

Idempotent: re-running is safe (cap is monotonic, only lowers scores).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from typing import Iterable

# Project root + shared on path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from shared.work_auth import apply_geo_score_cap  # noqa: E402


def _score_to_tier(score: float | None) -> str:
    if score is None:
        return ""
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _get_supabase():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY env required")
    return create_client(url, key)


def _chunk(seq: Iterable, n: int):
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write updates (default: dry-run)")
    parser.add_argument("--user-id", help="Limit to a single user (default: all users)")
    args = parser.parse_args(argv)

    db = _get_supabase()

    # Load all users with work_authorizations populated
    users_q = db.table("users").select("id, work_authorizations, location")
    if args.user_id:
        users_q = users_q.eq("id", args.user_id)
    users = users_q.execute().data or []
    print(f"[backfill] {len(users)} user(s) to process")

    total_changed = 0
    total_inspected = 0
    tier_changes: Counter[str] = Counter()

    for user in users:
        user_id = user["id"]
        wauth = user.get("work_authorizations") or {}
        if not wauth:
            print(f"  user {user_id[:8]}: no work_authorizations; skipping")
            continue

        # Page through jobs in chunks (range to avoid loading everything at once)
        page_size = 500
        offset = 0
        while True:
            page = (
                db.table("jobs")
                .select("job_id, location, description, "
                        "match_score, ats_score, hiring_manager_score, "
                        "tech_recruiter_score, score_tier, gaps")
                .eq("user_id", user_id)
                .range(offset, offset + page_size - 1)
                .execute()
                .data
            ) or []
            if not page:
                break
            offset += page_size

            for job in page:
                total_inspected += 1
                before = {
                    k: job.get(k) for k in (
                        "match_score", "ats_score", "hiring_manager_score",
                        "tech_recruiter_score", "gaps"
                    )
                }
                # Build a score_result dict the cap can mutate
                score_result = {
                    "match_score": job.get("match_score") or 0,
                    "ats_score": job.get("ats_score") or 0,
                    "hiring_manager_score": job.get("hiring_manager_score") or 0,
                    "tech_recruiter_score": job.get("tech_recruiter_score") or 0,
                    "gaps": list(job.get("gaps") or []),
                }
                capped = apply_geo_score_cap(score_result, job, wauth)
                # Check if anything changed
                changed = any(capped[k] != before[k] for k in (
                    "match_score", "ats_score", "hiring_manager_score", "tech_recruiter_score"
                )) or capped["gaps"] != before["gaps"]
                if not changed:
                    continue

                old_tier = job.get("score_tier") or _score_to_tier(before["match_score"])
                new_tier = _score_to_tier(capped["match_score"])
                tier_changes[f"{old_tier}->{new_tier}"] += 1
                total_changed += 1

                update = {
                    "match_score": capped["match_score"],
                    "ats_score": capped["ats_score"],
                    "hiring_manager_score": capped["hiring_manager_score"],
                    "tech_recruiter_score": capped["tech_recruiter_score"],
                    "score_tier": new_tier,
                    "gaps": capped["gaps"],
                }
                if args.commit:
                    db.table("jobs").update(update).eq("job_id", job["job_id"]).execute()
                elif total_changed <= 5:
                    print(f"    DRY-RUN would update {job['job_id'][:8]}: "
                          f"{old_tier} ({before['match_score']}) → {new_tier} ({capped['match_score']}) "
                          f"({job.get('location') or '?'})")

    print()
    print(f"[backfill] inspected {total_inspected} jobs, would change {total_changed}")
    print(f"[backfill] tier transitions: {dict(tier_changes)}")
    print(f"[backfill] mode: {'COMMIT' if args.commit else 'DRY-RUN (use --commit to write)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
