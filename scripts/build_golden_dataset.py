#!/usr/bin/env python3
"""Build golden dataset for QA scoring calibration.

Queries Supabase for scored jobs distributed across score tiers, combines
each with the base resume text, and writes 25 JD+resume pairs to
tests/quality/golden_dataset.json.

Distribution:
  - 5 S-tier  (90+)  → expected_label: strong_match
  - 8 A-tier  (80-89) → expected_label: good_match
  - 7 B-tier  (70-79) → expected_label: weak_match
  - 5 C/D-tier (<70)  → expected_label: no_match

Usage:
  python scripts/build_golden_dataset.py              # build dataset
  python scripts/build_golden_dataset.py --dry-run    # preview counts only
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

REGION = "eu-west-1"
USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"

ROOT = Path(__file__).parent.parent
OUTPUT_PATH = ROOT / "tests" / "quality" / "golden_dataset.json"

# How many pairs to collect per tier bucket
TIER_TARGETS = [
    # (label,          min_score, max_score, count)
    ("strong_match",   90,        100,       5),
    ("good_match",     80,        89,        8),
    ("weak_match",     70,        79,        7),
    ("no_match",        0,        69,        5),
]


def _get_ssm_credentials() -> tuple[str, str]:
    """Fetch Supabase credentials from SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=REGION)
    url = ssm.get_parameter(
        Name="/naukribaba/SUPABASE_URL", WithDecryption=True
    )["Parameter"]["Value"]
    key = ssm.get_parameter(
        Name="/naukribaba/SUPABASE_SERVICE_KEY", WithDecryption=True
    )["Parameter"]["Value"]
    return url, key


def _fetch_jobs_for_tier(
    db,
    min_score: int,
    max_score: int,
    count: int,
) -> list[dict]:
    """Fetch up to `count` scored jobs in [min_score, max_score] range.

    Ordered by match_score descending so we get the most representative
    examples near the top of each tier band.
    """
    result = (
        db.table("jobs")
        .select(
            "job_hash, title, company, description, match_score, "
            "base_ats_score, base_hm_score, base_tr_score, score_tier"
        )
        .eq("user_id", USER_ID)
        .gte("match_score", min_score)
        .lte("match_score", max_score)
        .not_.is_("match_score", "null")
        .order("match_score", desc=True)
        .limit(count)
        .execute()
    )
    return result.data or []


def _enrich_descriptions(db, jobs: list[dict]) -> dict[str, str]:
    """Look up descriptions from jobs_raw for jobs that have no description."""
    hashes_missing = [
        j["job_hash"] for j in jobs
        if j.get("job_hash") and not j.get("description")
    ]
    if not hashes_missing:
        return {}

    result = (
        db.table("jobs_raw")
        .select("job_hash, description")
        .in_("job_hash", hashes_missing)
        .execute()
    )
    return {
        r["job_hash"]: r["description"]
        for r in (result.data or [])
        if r.get("description")
    }


def _get_resume_excerpt(db) -> str:
    """Fetch the latest base resume tex_content for the user."""
    result = (
        db.table("user_resumes")
        .select("tex_content")
        .eq("user_id", USER_ID)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data or not result.data[0].get("tex_content"):
        raise RuntimeError("No base resume found in user_resumes for this user.")
    return result.data[0]["tex_content"]


def _build_pair(
    job: dict,
    resume_excerpt: str,
    expected_label: str,
    raw_desc_by_hash: dict[str, str],
) -> dict:
    """Build a single golden pair dict from a job row and resume text."""
    description = job.get("description") or raw_desc_by_hash.get(job.get("job_hash", ""), "")

    return {
        "job_hash": job.get("job_hash", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "description": description[:2000],
        "resume_excerpt": resume_excerpt[:1000],
        "scores": {
            "ats": job.get("base_ats_score") or 0,
            "hm": job.get("base_hm_score") or 0,
            "tr": job.get("base_tr_score") or 0,
            "match": job.get("match_score") or 0,
        },
        "expected_label": expected_label,
        "needs_review": True,
    }


def main(dry_run: bool) -> None:
    print("Connecting to Supabase via SSM credentials...")
    url, key = _get_ssm_credentials()

    from supabase import create_client
    db = create_client(url, key)

    print("Fetching base resume...")
    resume_text = _get_resume_excerpt(db)
    print(f"  Resume: {len(resume_text)} chars")

    all_jobs: list[dict] = []
    tier_summary: list[str] = []

    for label, min_score, max_score, target_count in TIER_TARGETS:
        jobs = _fetch_jobs_for_tier(db, min_score, max_score, target_count)
        tier_summary.append(
            f"  {label:15s} [{min_score:3d}-{max_score:3d}]: "
            f"found {len(jobs)}/{target_count}"
        )
        all_jobs.extend([(job, label) for job in jobs])

    print("\nTier distribution:")
    for line in tier_summary:
        print(line)

    total = len(all_jobs)
    print(f"\nTotal pairs collected: {total}")

    if dry_run:
        print("\n[DRY RUN] Would write these pairs:")
        for job, label in all_jobs:
            score = job.get("match_score", 0)
            has_desc = bool(job.get("description"))
            print(
                f"  [{label:15s}] score={score:3d}  "
                f"desc={'yes' if has_desc else 'NO '}  "
                f"{job.get('title', '?')[:40]} @ {job.get('company', '?')[:25]}"
            )
        return

    # Enrich descriptions from jobs_raw for jobs that are missing them
    raw_desc_by_hash = _enrich_descriptions(db, [j for j, _ in all_jobs])
    enriched = sum(1 for j, _ in all_jobs if not j.get("description") and j.get("job_hash", "") in raw_desc_by_hash)
    if enriched:
        print(f"  Enriched {enriched} missing descriptions from jobs_raw")

    # Build the pairs list
    pairs = [
        _build_pair(job, resume_text, label, raw_desc_by_hash)
        for job, label in all_jobs
    ]

    # Report pairs with no description (these will need manual attention)
    no_desc = [p for p in pairs if not p["description"]]
    if no_desc:
        print(f"\nWarning: {len(no_desc)} pairs have empty descriptions (needs_review=true is already set):")
        for p in no_desc:
            print(f"  - {p['title'][:40]} @ {p['company']}")

    dataset = {
        "_comment": "Golden dataset: 25 JD+resume pairs, human-labeled. Utkarsh must label these from dashboard jobs.",
        "_instructions": (
            "Review each pair. Confirm or adjust expected_label. "
            "Set needs_review=false once you've verified the label is correct."
        ),
        "_created": "2026-04-04",
        "_generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_total_pairs": len(pairs),
        "pairs": pairs,
    }

    OUTPUT_PATH.write_text(json.dumps(dataset, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(pairs)} pairs to {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build golden dataset for QA scoring calibration")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts and pairs without writing")
    args = parser.parse_args()
    main(args.dry_run)
