#!/usr/bin/env python3
"""Score jobs that were scraped, are scoreable, and never became a `jobs` row.

The daily pipeline scores only the hashes MergeAndDedup calls new. A job
scraped on Monday and skipped — because scoring errored, or because it scored
below the threshold and the result was discarded (fixed 2026-10-07) — is never
reconsidered, because on Tuesday it is no longer new. There is no retry path.

Measured 2026-10-07 over the previous 14 days:

    scraped                : 2196
    of which scoreable     : 1131
    already in `jobs`      :  158
    NEVER promoted         :  973

973 jobs the user could have seen and did not. This recovers them.

It is deliberately a script and not a new state machine branch: the selection
query is the interesting part and it belongs somewhere a human can read, run
and check before it is wired into a schedule. Promoting it into the daily
pipeline is the follow-up, and this is the thing that proves the query first.

Usage:
    python scripts/score_unpromoted.py --days 14            # dry run
    python scripts/score_unpromoted.py --days 14 --commit
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "lambdas" / "pipeline"))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

SCORE_FUNCTION = "naukribaba-score-batch"
REGION = "eu-west-1"
CHUNK = 10          # matches the state machine's ChunkJobHashes chunk_size


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


def unpromoted(url, headers, days: int) -> list[str]:
    """Scoreable hashes in jobs_raw with no row in jobs.

    Scoreability is decided by the pipeline's OWN `should_skip_scoring`, not by
    a copy of its rules here. A second implementation would drift, and the
    population this selects has to be exactly the one the scorer would accept.
    """
    import httpx

    from score_batch import should_skip_scoring

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).isoformat()
    raw, off = [], 0
    while True:
        page = httpx.get(f"{url}/rest/v1/jobs_raw", params={
            "select": "job_hash,title,company,description",
            "scraped_at": f"gte.{since}", "limit": 1000, "offset": off,
        }, headers=headers, timeout=60).json()
        if not isinstance(page, list) or not page:
            break
        raw += page
        if len(page) < 1000:
            break
        off += 1000

    scoreable = [r["job_hash"] for r in raw
                 if r.get("job_hash") and should_skip_scoring(r) is None]
    have = set()
    for i in range(0, len(scoreable), 100):
        chunk = scoreable[i:i + 100]
        rows = httpx.get(f"{url}/rest/v1/jobs", params={
            "select": "job_hash", "job_hash": f"in.({','.join(chunk)})", "limit": 1000,
        }, headers=headers, timeout=60).json()
        have |= {r["job_hash"] for r in rows if isinstance(r, dict)}

    print(f"  scraped in {days}d : {len(raw)}")
    print(f"  scoreable        : {len(scoreable)}")
    print(f"  already promoted : {len(have)}")
    return [h for h in scoreable if h not in have]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max", type=int, default=None, dest="max_jobs")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--min-score", type=int, default=70,
                    help="governs TAILORING only; every scored job is stored "
                         "regardless (see score_batch)")
    ap.add_argument("--stagger", type=float, default=40.0,
                    help="seconds between chunks. Scoring batches 10 jobs into "
                         "one prompt and the pool shares a Groq TPM ceiling")
    args = ap.parse_args()

    url, headers = _db()
    user_id = args.user_id or _one_user(url, headers)
    todo = unpromoted(url, headers, args.days)
    if args.max_jobs:
        todo = todo[:args.max_jobs]
    chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]
    print(f"\n{len(todo)} unpromoted job(s) -> {len(chunks)} chunk(s) of {CHUNK}")

    if not args.commit:
        print("\nDRY RUN — nothing invoked. Re-run with --commit.")
        return 0
    if not todo:
        return 0

    import boto3

    lam = boto3.client("lambda", region_name=REGION)
    sent = failed = 0
    for i, chunk in enumerate(chunks, 1):
        try:
            resp = lam.invoke(
                FunctionName=SCORE_FUNCTION, InvocationType="Event",
                Payload=json.dumps({"user_id": user_id, "new_job_hashes": chunk,
                                    "min_match_score": args.min_score}).encode(),
            )
            ok = resp.get("StatusCode") == 202
            sent += ok
            failed += not ok
            print(f"  [{i}/{len(chunks)}] {'queued' if ok else 'FAILED'} {len(chunk)} job(s)",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not stop the run
            failed += 1
            print(f"  [{i}/{len(chunks)}] FAILED: {exc}", flush=True)
        if i < len(chunks):
            time.sleep(args.stagger)

    print(f"\nqueued {sent} chunk(s), failed {failed}")
    print("Asynchronous: a 202 means ACCEPTED. Verify by re-running this script "
          "— the unpromoted count should fall.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
