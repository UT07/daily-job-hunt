#!/usr/bin/env python3
"""Backfill missing resume/cover-letter artifacts.

Two phases. **Run audit first**, review output, then run backfill with --apply.

Phase 1 — audit (default): walks jobs that should have artifacts but don't,
and prints counts + sample rows. Read-only. No side effects.

Phase 2 — backfill (--apply): for each affected job, triggers the
single-job pipeline (re-tailor) so a fresh artifact is generated. Uses
the existing /api/pipeline/re-tailor/{job_id} endpoint pattern so all
the bug fixes (header-marker per-user, save_job failure visibility,
SFN job_hash plumbing) are exercised on retry.

Usage:
    # Audit only (recommended first):
    python scripts/backfill_missing_artifacts.py

    # After reviewing audit, apply backfill:
    python scripts/backfill_missing_artifacts.py --apply --max-jobs 20

    # Limit by tier (defaults to S+A):
    python scripts/backfill_missing_artifacts.py --apply --tier S

Environment:
    SUPABASE_URL, SUPABASE_SERVICE_KEY  — Supabase admin access
    AWS_REGION (default eu-west-1)       — for SFN start
    SINGLE_JOB_PIPELINE_ARN              — SFN ARN (read from env or stack)

DESTRUCTIVE in --apply mode. Triggers AI calls (cost) and reassigns rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

try:
    from supabase import create_client
    import boto3
except ImportError:
    print("ERROR: pip install supabase boto3", file=sys.stderr)
    sys.exit(1)


def _get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: set SUPABASE_URL + SUPABASE_SERVICE_KEY", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def _get_sfn():
    region = os.environ.get("AWS_REGION", "eu-west-1")
    return boto3.client("stepfunctions", region_name=region)


def audit(sb, tiers: list[str], max_rows: int) -> list[dict[str, Any]]:
    """Find jobs that should have artifacts but don't."""
    print(f"[audit] querying jobs in tiers={tiers}, missing resume_s3_url, not expired...")
    rows = (
        sb.table("jobs")
        .select("job_id, job_hash, user_id, title, company, score_tier, "
                "match_score, application_status, tailoring_error, created_at")
        .in_("score_tier", tiers)
        .is_("resume_s3_url", "null")
        .eq("is_expired", False)
        .order("created_at", desc=True)
        .limit(max_rows)
        .execute()
    )
    return rows.data or []


def reassign_default_user(sb, real_user_id: str, dry_run: bool = True) -> int:
    """Population A from postmortem: jobs landed under user_id='default' due
    to the daily EventBridge cron bug. Reassign to the real user."""
    res = (
        sb.table("jobs_raw").select("job_hash", count="exact")
        .eq("user_id", "default").limit(1).execute()
    )
    total = res.count or 0
    print(f"[reassign] found {total} jobs_raw rows with user_id='default'")

    res2 = (
        sb.table("jobs").select("job_id", count="exact")
        .eq("user_id", "default").limit(1).execute()
    )
    total2 = res2.count or 0
    print(f"[reassign] found {total2} jobs rows with user_id='default'")

    if dry_run:
        print("[reassign] DRY RUN — no changes. Pass --apply to reassign.")
        return total + total2

    print(f"[reassign] reassigning to user_id={real_user_id}...")
    sb.table("jobs_raw").update({"user_id": real_user_id}).eq("user_id", "default").execute()
    sb.table("jobs").update({"user_id": real_user_id}).eq("user_id", "default").execute()
    return total + total2


def backfill_one(sfn, sm_arn: str, user_id: str, job_hash: str) -> str | None:
    """Start a single-job re-tailor SFN execution."""
    try:
        resp = sfn.start_execution(
            stateMachineArn=sm_arn,
            input=json.dumps({
                "user_id": user_id,
                "job_hash": job_hash,
                "skip_scoring": True,
            }),
        )
        return resp["executionArn"].split(":")[-1]
    except Exception as e:
        print(f"  [start_execution failed for {job_hash}: {e}]", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--tier", default="SA")
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--reassign-default-user", default=None)
    parser.add_argument("--reassign-only", action="store_true")
    args = parser.parse_args()

    tiers = ["S", "A"] if args.tier == "SA" else [args.tier]
    sb = _get_supabase()

    if args.reassign_default_user:
        n = reassign_default_user(sb, args.reassign_default_user, dry_run=not args.apply)
        print(f"[reassign] {'applied' if args.apply else 'audit'}: {n} rows")

    if args.reassign_only:
        return 0

    affected = audit(sb, tiers, max_rows=max(args.max_jobs * 5, 50))
    print()
    print(f"[audit] {len(affected)} job(s) without resume_s3_url:")
    print("=" * 100)
    for j in affected[:20]:
        err = (j.get("tailoring_error") or "")[:50]
        print(f"  {j['score_tier']} | score={j.get('match_score'):>3} | "
              f"{(j.get('title') or '')[:40]:<40} @ {(j.get('company') or '')[:25]:<25} | "
              f"status={(j.get('application_status') or ''):<20} | err={err}")
    if len(affected) > 20:
        print(f"  ... and {len(affected) - 20} more")
    print("=" * 100)

    candidates = [j for j in affected if not j.get("tailoring_error")]
    print(f"\n[backfill] {len(candidates)} candidate(s) without prior tailoring_error")

    if not args.apply:
        print("\n[backfill] DRY RUN. Pass --apply to actually trigger SFN executions.")
        return 0

    sm_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN", "")
    if not sm_arn:
        print("ERROR: set SINGLE_JOB_PIPELINE_ARN", file=sys.stderr)
        return 1

    sfn = _get_sfn()
    triggered = 0
    for j in candidates[: args.max_jobs]:
        name = backfill_one(sfn, sm_arn, j["user_id"], j["job_hash"])
        if name:
            triggered += 1
            print(f"  ✓ triggered: {j['job_hash'][:12]} -> exec={name}")
        time.sleep(1.0)

    print(f"\n[backfill] triggered {triggered}/{min(len(candidates), args.max_jobs)} executions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
