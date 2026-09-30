#!/usr/bin/env python3
"""Re-tailor resumes in bulk through the PRODUCTION pipeline.

Why this exists. scripts/regenerate_tier_artifacts.py composes via the legacy
`tailorer` and cannot apply users.composition_policy -- it now refuses for that
reason (see its main()). The pipeline handler
lambdas/pipeline/tailor_resume.handler DOES read the policy: caps, the ORDER
rule, `prefer`, `writing` and `emphasise` all reach the prompt through
shared/composition_policy.render_for_prompt. So bulk regeneration has to invoke
the Lambda, not the script.

Invocation is ASYNCHRONOUS on purpose. Tailoring takes 60-90s per job (one
council call plus up to two repair rounds plus a compile), and boto3's default
read timeout is 60s -- a synchronous invoke reports a ReadTimeoutError for a job
that then completes anyway, which is a lie in the least useful direction. `Event`
returns a 202 immediately and the Lambda runs to completion on its own.

Usage:
    python scripts/retailor_bulk.py --tier S                 # dry run, lists jobs
    python scripts/retailor_bulk.py --tier S --commit
    python scripts/retailor_bulk.py --tier S,A --commit --max 5
    python scripts/retailor_bulk.py --check                  # progress since a run

Requires AWS credentials for lambda:InvokeFunction and the Supabase service key.
It does not need a user session token, which is the point -- the alternative
(POST /api/pipeline/re-tailor) needs one and cannot be driven from a script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

FUNCTION = "naukribaba-tailor-resume"
REGION = "eu-west-1"


def _db():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY env required")
    return url.rstrip("/"), {"apikey": key, "Authorization": f"Bearer {key}"}


def _one_user(url, headers) -> str:
    import httpx

    rows = httpx.get(f"{url}/rest/v1/users", params={"select": "id", "limit": 2},
                     headers=headers, timeout=30).json()
    if len(rows) != 1:
        raise SystemExit(f"expected exactly one user, found {len(rows)} — pass --user-id")
    return rows[0]["id"]


def select_jobs(url, headers, user_id: str, tiers: list[str], max_jobs: int | None):
    """Active, non-expired jobs in `tiers` that the handler can actually read.

    Filtered against jobs_raw because handler() reads the job from there and
    raises TailorError for anything absent -- invoking for those would burn a
    cold start and an AI call to produce a failure.
    """
    import httpx

    jobs, offset = [], 0
    while True:
        page = httpx.get(f"{url}/rest/v1/jobs", params={
            "select": "job_hash,title,company,score_tier,resume_s3_url",
            "user_id": f"eq.{user_id}", "is_expired": "eq.false",
            "score_tier": f"in.({','.join(tiers)})",
            "order": "match_score.desc", "limit": 1000, "offset": offset,
        }, headers=headers, timeout=60).json()
        if not isinstance(page, list) or not page:
            break
        jobs += page
        if len(page) < 1000:
            break
        offset += 1000

    hashes = [j["job_hash"] for j in jobs]
    present: set[str] = set()
    for i in range(0, len(hashes), 100):
        chunk = hashes[i:i + 100]
        rows = httpx.get(f"{url}/rest/v1/jobs_raw", params={
            "select": "job_hash", "job_hash": f"in.({','.join(chunk)})", "limit": 1000,
        }, headers=headers, timeout=60).json()
        present |= {r["job_hash"] for r in rows if isinstance(r, dict)}

    usable = [j for j in jobs if j["job_hash"] in present]
    skipped = len(jobs) - len(usable)
    if skipped:
        print(f"  {skipped} job(s) skipped: not in jobs_raw, handler would raise")
    return usable[:max_jobs] if max_jobs else usable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="S,A", help="comma-separated tiers (default S,A)")
    ap.add_argument("--max", type=int, default=None, dest="max_jobs")
    ap.add_argument("--commit", action="store_true", help="actually invoke (default dry run)")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--depth", default="moderate", choices=["light", "moderate", "full"])
    ap.add_argument("--stagger", type=float, default=6.0,
                    help="seconds between invocations; the council shares a Groq "
                         "TPM ceiling, so firing 30 at once mostly produces 429s")
    args = ap.parse_args()

    url, headers = _db()
    user_id = args.user_id or _one_user(url, headers)
    tiers = [t.strip().upper() for t in args.tier.split(",") if t.strip()]

    jobs = select_jobs(url, headers, user_id, tiers, args.max_jobs)
    print(f"{len(jobs)} job(s) in tier(s) {','.join(tiers)} for user {user_id[:8]}")
    for j in jobs[:10]:
        print(f"  {j['score_tier']}  {j['job_hash'][:12]}  {j['title'][:38]:40s} {j['company'][:22]}")
    if len(jobs) > 10:
        print(f"  ... and {len(jobs) - 10} more")

    if not args.commit:
        print("\nDRY RUN — nothing invoked. Re-run with --commit.")
        return 0
    if not jobs:
        return 0

    import boto3

    lam = boto3.client("lambda", region_name=REGION)
    sent = failed = 0
    for i, j in enumerate(jobs, 1):
        try:
            # InvocationType="Event": returns 202 at once. A synchronous invoke
            # times out at 60s on a 60-90s job and reports failure for work that
            # succeeds.
            resp = lam.invoke(
                FunctionName=FUNCTION, InvocationType="Event",
                Payload=json.dumps({"job_hash": j["job_hash"], "user_id": user_id,
                                    "tailoring_depth": args.depth}).encode(),
            )
            code = resp.get("StatusCode")
            if code == 202:
                sent += 1
                print(f"  [{i}/{len(jobs)}] queued {j['job_hash'][:12]} {j['title'][:34]}")
            else:
                failed += 1
                print(f"  [{i}/{len(jobs)}] UNEXPECTED StatusCode {code} for {j['job_hash'][:12]}")
        except Exception as exc:  # noqa: BLE001 — one bad job must not stop the batch
            failed += 1
            print(f"  [{i}/{len(jobs)}] FAILED to queue {j['job_hash'][:12]}: {exc}")
        if i < len(jobs):
            time.sleep(args.stagger)

    print(f"\nqueued {sent}, failed to queue {failed}")
    print("Asynchronous: a 202 means ACCEPTED, not finished. Each job takes "
          "60-90s. Check progress with:")
    print("  aws logs tail /aws/lambda/naukribaba-tailor-resume --since 15m --format short | grep '\\[tailor\\]'")
    print("and verify the artifacts with scripts/smoke_prod.py.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
