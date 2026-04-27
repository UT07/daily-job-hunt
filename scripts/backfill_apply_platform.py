"""One-shot backfill: classify apply_platform for jobs where it's NULL.

Idempotent: only touches rows where apply_platform IS NULL. Re-runnable.

Usage (from repo root):
    source .venv/bin/activate
    python scripts/backfill_apply_platform.py            # dry-run (default)
    python scripts/backfill_apply_platform.py --commit   # actually write

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from .env (project root).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from typing import Iterable, List

from dotenv import load_dotenv
from supabase import create_client

# Make `shared` importable when run from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.apply_platform import classify_apply_platform  # noqa: E402


CHUNK_SIZE = 100


def _chunked(seq: List[tuple[str, str]], n: int) -> Iterable[List[tuple[str, str]]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Write updates (default: dry-run)")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    sb = create_client(url, key)

    rows: list[dict] = []
    PAGE = 1000
    offset = 0
    while True:
        page = (
            sb.table("jobs")
            .select("job_id, apply_url")
            .is_("apply_platform", "null")
            .not_.is_("apply_url", "null")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        if not page.data:
            break
        rows.extend(page.data)
        offset += PAGE
        if len(page.data) < PAGE:
            break

    print(f"Candidates: {len(rows)} jobs with apply_platform IS NULL and apply_url IS NOT NULL")

    classified: list[tuple[str, str]] = []
    dist: Counter = Counter()
    for r in rows:
        platform = classify_apply_platform(r.get("apply_url") or "")
        if platform:
            classified.append((r["job_id"], platform))
            dist[platform] += 1

    dist["<unmatched>"] = len(rows) - len(classified)
    print("\nClassification result:")
    for k, v in dist.most_common():
        print(f"  {k:<25} {v:>5}")

    if not args.commit:
        print(f"\nDry-run complete. {len(classified)} would be updated. Re-run with --commit to write.")
        return 0

    print(f"\nWriting {len(classified)} updates in chunks of {CHUNK_SIZE}...")
    written = 0
    for chunk in _chunked(classified, CHUNK_SIZE):
        for job_id, platform in chunk:
            sb.table("jobs").update({"apply_platform": platform}).eq("job_id", job_id).execute()
            written += 1
        print(f"  wrote {written}/{len(classified)}")

    print(f"\nDone. {written} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
