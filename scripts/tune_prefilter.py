#!/usr/bin/env python3
"""Tune merge_dedup._prefilter_job against the real jobs_raw corpus.

Read-only analysis tool. Never writes to the database. Imports the actual
`merge_dedup` module fresh on every run, so this always measures whatever
prefilter logic is currently in the file -- not a copy that can drift from
what's shipped. The intended workflow is: run this, look at the reject-
reason breakdown and the admitted/rejected samples, edit
lambdas/pipeline/merge_dedup.py, run this again, repeat until the admitted
volume and the sampled quality both look right.

PostgREST caps a single response at 1000 rows by default -- this has bitten
this project three times already (see scripts/backfill_job_embeddings.py,
scripts/tune_dedup_threshold.py). jobs_raw has 12,672+ rows, so this script
paginates with .range() and prints the total it actually fetched next to
Supabase's own count="exact" total as a cross-check that nothing was
silently truncated.

Freshness note: _prefilter_job's Rule 0 compares posted_date against
datetime.now() -- correct in production, where merge_dedup always runs
against jobs scraped moments earlier. Applied naively to a corpus scraped
across the last five months, that would make Rule 0 reject almost
everything and hide how the other rules behave. To measure "how stale was
this job when the pipeline actually would have seen it", each job's
posted_date is shifted so that (synthetic_posted_date, now) reproduces the
same age gap as (real posted_date, scraped_at) -- i.e. we replay each job
at the same freshness it actually had on the day it was scraped. This
shift is analysis-only, applied to a copy of the row before calling the
real _prefilter_job; nothing in merge_dedup.py is touched or monkeypatched.

Usage:
    source .venv/bin/activate
    python scripts/tune_prefilter.py                 # full report
    python scripts/tune_prefilter.py --samples 15     # more example rows
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Load .env the same way every other scripts/*.py does.
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

# lambdas/pipeline goes on sys.path (not `lambdas.pipeline...`) -- same
# reasoning as scripts/tune_dedup_threshold.py: that's how the flattened
# zip-Lambda import path resolves, and a test forbids the dotted form.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from db_client import SupabaseClient  # noqa: E402

# Same cap discipline as the other tuning scripts in this repo.
PAGE = 1000

SELECT_COLS = "job_hash, title, company, source, description, location, posted_date, scraped_at"


def _fetch_all_jobs_raw(db) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            db.table("jobs_raw")
            .select(SELECT_COLS)
            .order("job_hash")  # stable order so .range() pagination can't skip/repeat rows
            .range(offset, offset + PAGE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def _replay_posted_date(job: dict, now: datetime) -> dict:
    """Return a copy of `job` with posted_date shifted to reproduce the same
    scrape-time freshness gap, relative to `now`, instead of the real one.

    scraped_at/posted_date missing or unparseable -> leave posted_date as-is
    (None stays None, which _prefilter_job already treats as "pass Rule 0").
    """
    posted = job.get("posted_date")
    scraped = job.get("scraped_at")
    if not posted or not scraped:
        return job
    try:
        p = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
        s = datetime.fromisoformat(str(scraped).replace("Z", "+00:00"))
    except ValueError:
        return job
    if p.tzinfo is None:
        p = p.replace(tzinfo=timezone.utc)
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    age_at_scrape = s - p
    synthetic = dict(job)
    synthetic["posted_date"] = (now - age_at_scrape).isoformat()
    return synthetic


def _reason_bucket(reason: str) -> str:
    """Collapse a specific reason ('too_senior:director') to its rule name
    ('too_senior') for a compact breakdown table."""
    return reason.split(":", 1)[0]


def main(samples: int) -> None:
    import merge_dedup  # imported AFTER sys.path is set up, and fresh every run

    db = SupabaseClient.from_env().client

    exact = db.table("jobs_raw").select("job_hash", count="exact").limit(1).execute().count
    rows = _fetch_all_jobs_raw(db)
    print(f"jobs_raw: fetched {len(rows)} rows via pagination; count(exact)={exact}")
    if len(rows) != exact:
        print("  MISMATCH -- pagination did not retrieve every row, stop and investigate")
        return

    by_source = Counter(r.get("source") for r in rows)
    print(f"\nBy source: {dict(by_source.most_common())}")

    now = datetime.now(timezone.utc)
    user_skills = merge_dedup.DEFAULT_USER_SKILLS

    admitted, rejected = [], []
    reject_reason_counts = Counter()
    per_day_total = Counter()
    per_day_admit = Counter()

    for row in rows:
        sim_job = _replay_posted_date(row, now)
        passes, reason = merge_dedup._prefilter_job(sim_job, user_skills)
        day = (row.get("scraped_at") or "")[:10]
        per_day_total[day] += 1
        if passes:
            admitted.append(row)
            per_day_admit[day] += 1
        else:
            rejected.append((row, reason))
            reject_reason_counts[_reason_bucket(reason)] += 1

    total = len(rows)
    print(f"\n=== Current _prefilter_job on {total} real jobs_raw rows (replayed at scrape-time freshness) ===")
    print(f"Admitted: {len(admitted)} ({100 * len(admitted) / total:.1f}%)")
    print(f"Rejected: {len(rejected)} ({100 * len(rejected) / total:.1f}%)")
    print("\nReject reason breakdown:")
    for reason, count in reject_reason_counts.most_common():
        print(f"  {reason:30s} {count:6d}  ({100 * count / total:.1f}%)")

    # Per-day volume, sorted by total descending -- the days that actually
    # stress-test "how many pass on a big day", not just the average.
    print("\nTop 15 days by raw volume, admitted count under current filter:")
    print(f"  {'date':12s} {'raw':>6s} {'admitted':>9s}  admit_rate")
    for day, total_day in per_day_total.most_common(15):
        adm = per_day_admit.get(day, 0)
        rate = 100 * adm / total_day if total_day else 0.0
        print(f"  {day:12s} {total_day:6d} {adm:9d}  {rate:5.1f}%")

    active_days = [d for d in per_day_total if d]
    if active_days:
        mean_admit_per_day = sum(per_day_admit.values()) / len(active_days)
        print(f"\nMean admitted/day across {len(active_days)} distinct scrape days: {mean_admit_per_day:.1f}")

    print(f"\n=== {min(samples, len(admitted))} sampled ADMITTED jobs ===")
    for row in admitted[:: max(1, len(admitted) // max(samples, 1))][:samples]:
        print(f"  PASS  [{row.get('source')}] {row.get('title')!r} @ {row.get('company')!r} | loc={row.get('location')!r}")

    print(f"\n=== {min(samples, len(rejected))} sampled REJECTED jobs (spread across reasons) ===")
    by_reason: dict[str, list] = {}
    for row, reason in rejected:
        by_reason.setdefault(_reason_bucket(reason), []).append((row, reason))
    per_reason_n = max(1, samples // max(len(by_reason), 1))
    for bucket, items in by_reason.items():
        for row, reason in items[:per_reason_n]:
            print(f"  REJECT[{reason:28s}] [{row.get('source')}] {row.get('title')!r} @ {row.get('company')!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=20)
    args = p.parse_args()
    main(args.samples)
