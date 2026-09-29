#!/usr/bin/env python3
"""Print Studio suggestions for real jobs, so they can be READ before being trusted.

The Resume Studio spec is blunt about this phase (§10): "Suggestion quality is
unproven. No suggestion model exists yet. Phase 3 should begin by generating
suggestions for ten real jobs and reading them before any UI is built — a panel
full of generic advice is worse than no panel."

The UI cannot answer that question. A panel renders whatever it is given, and
green tests prove the anchoring is correct, not that the advice is any good.
This script is the smallest thing that puts the actual output in front of a
human: no browser, no compile, no writes of any kind.

    python scripts/preview_suggestions.py --count 10
    python scripts/preview_suggestions.py --job-id <uuid>

Reads only. It never writes to Supabase or S3.
"""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

env_path = REPO / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import boto3  # noqa: E402

from db_client import SupabaseClient  # noqa: E402
from lambdas.pipeline.parse_sections import parse_resume_sections  # noqa: E402
from lambdas.pipeline.suggest_sections import (  # noqa: E402
    candidate_targets,
    generate_suggestions,
)

DEFAULT_USER = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"


def _fetch_tex(s3, bucket: str, user_id: str, job_hash: str) -> str | None:
    """The tailored .tex, keyed by job_hash — see app._tailored_tex_key."""
    key = f"users/{user_id}/resumes/{job_hash}_tailored.tex"
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception as e:
        print(f"    (no .tex at {key}: {type(e).__name__})")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--job-id", default=None, help="one specific job instead of a sample")
    ap.add_argument("--user", default=os.environ.get("SUPABASE_USER_ID", DEFAULT_USER))
    args = ap.parse_args()

    db = SupabaseClient.from_env()
    bucket = os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    query = (
        db.client.table("jobs")
        .select("job_id, job_hash, title, company, description")
        .eq("user_id", args.user)
        .not_.is_("resume_s3_url", "null")
    )
    if args.job_id:
        query = query.eq("job_id", args.job_id)
    else:
        query = query.order("match_score", desc=True).limit(args.count)
    jobs = query.execute().data or []

    print(f"{len(jobs)} job(s), user {args.user}, bucket {bucket}\n")

    total = 0
    for job in jobs:
        print("=" * 78)
        print(f"{job.get('title')} @ {job.get('company')}  [{job.get('job_id')}]")
        jd = job.get("description") or ""
        if not jd.strip():
            print("    (no description — nothing to suggest against)")
            continue

        tex = _fetch_tex(s3, bucket, args.user, job.get("job_hash") or job["job_id"])
        if not tex:
            continue

        sections = parse_resume_sections(tex)
        targets = candidate_targets(sections)
        print(f"    {len(targets)} candidate line(s)")

        suggestions = generate_suggestions(sections, jd)
        total += len(suggestions)
        if not suggestions:
            print("    NO SUGGESTIONS")
        for s in suggestions:
            print()
            print(f"    {s['label']}")
            print(f"      now : {s['anchor_text']}")
            print(f"      why : {s['why']}")
            print(f"      new : {s['replacement']}")
        print()

    print("=" * 78)
    print(f"{total} suggestion(s) across {len(jobs)} job(s)")
    print("Read them. If they are generic, the prompt is wrong — not the UI.")


if __name__ == "__main__":
    main()
