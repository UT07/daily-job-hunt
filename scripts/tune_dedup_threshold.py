#!/usr/bin/env python3
"""Sweep the semantic dedup threshold against production data.

Prints, per threshold, how many pairs would be merged. Inspect the pairs at
each level and pick the highest threshold that still catches the known
TREQS-style duplicates without merging distinct roles.

Usage:
    source .venv/bin/activate
    python scripts/tune_dedup_threshold.py
"""
import sys
from pathlib import Path

# See scripts/backfill_job_embeddings.py for why lambdas/pipeline goes on
# sys.path instead of importing via `lambdas.pipeline...` -- that spelling
# cannot resolve once CodeUri flattens lambdas/pipeline/ into a zip Lambda's
# /var/task, and a test now forbids it anywhere under that directory.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from ai_helper import get_supabase  # noqa: E402
from retrieval.store import cosine  # noqa: E402

THRESHOLDS = [0.88, 0.90, 0.92, 0.93, 0.95, 0.97]

# PostgREST caps a single response at 1000 rows by default -- confirmed live
# against this project. Fetching embedded jobs is the whole point of this
# script, so silently truncating past row 1000 would tune the threshold
# against a biased subset instead of the real corpus. Paginate instead (same
# fix as scripts/backfill_job_embeddings.py's _fetch_jobs_without_embeddings).
PAGE = 1000


def _fetch_embedded_jobs(db) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = db.table("jobs").select("job_hash, company, title, embedding") \
            .not_.is_("embedding", "null").range(offset, offset + PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def main() -> None:
    db = get_supabase()
    rows = _fetch_embedded_jobs(db)
    print(f"{len(rows)} jobs with embeddings")

    by_company: dict[str, list[dict]] = {}
    for row in rows:
        by_company.setdefault(row["company"], []).append(row)

    for threshold in THRESHOLDS:
        pairs = []
        for company, jobs in by_company.items():
            for i in range(len(jobs)):
                for j in range(i + 1, len(jobs)):
                    sim = cosine(jobs[i]["embedding"], jobs[j]["embedding"])
                    if sim >= threshold:
                        pairs.append((company, jobs[i]["title"], jobs[j]["title"], round(sim, 3)))
        print(f"\nthreshold {threshold}: {len(pairs)} pairs")
        for p in pairs[:5]:
            print("   ", p)


if __name__ == "__main__":
    main()
