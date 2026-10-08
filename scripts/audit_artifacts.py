#!/usr/bin/env python
"""Every artifact, against both the row AND the object store. Read-only.

WHY THIS EXISTS, AND WHY IT COVERS MORE THAN RESUMES

On 2026-10-07 an audit of this corpus reported "missing 0" while 212 résumés
were unopenable. It read S3; the dashboard reads the row, and
`jobs.resume_s3_key` was NULL for 212 of them. Measuring the artefact is not
measuring the user's view of it (CLAUDE.md #7).

That audit also only looked at RESUMES. Cover letters and contacts are produced
by the same pipeline, stored the same way, and were never checked at all — so
the same defect could have been sitting in them undetected. A one-artifact
audit of a three-artifact pipeline answers a question nobody asked.

So this checks every artifact a job can carry, and checks each one from BOTH
sides:

    row says yes + object exists   fine
    row says yes + object missing  a broken link: the user clicks and gets 403
    row says no  + object exists   invisible: it was generated and cannot be
                                   opened — the 212 case
    row says no  + object missing  genuinely not generated

The last is the only one that is merely absent rather than wrong, and even that
is worth counting: an S/A job with no cover letter is a gap in the product, not
a bug in it.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import boto3  # noqa: E402
import httpx  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("_rb", ROOT / "scripts/retailor_bulk.py")
_rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rb)

# (label, row key column, row url column, S3 key template)
ARTIFACTS = [
    ("resume", "resume_s3_key", "resume_s3_url", "users/{uid}/resumes/{h}_tailored.pdf"),
    ("cover letter", "cover_letter_s3_key", "cover_letter_s3_url",
     "users/{uid}/cover_letters/{h}_cover.pdf"),
]
# Artifacts that live entirely in a column — no object store, so "does the row
# have it" is the whole question.
COLUMN_ONLY = ["linkedin_contacts", "interview_prep", "company_research"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="S,A")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--all-tiers", action="store_true",
                    help="every tier, not just the ones the product promises artifacts for")
    args = ap.parse_args()

    url, headers = _rb._db()
    uid = args.user_id or _rb._one_user(url, headers)
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    params = {
        "select": ",".join(["job_hash", "title", "company", "score_tier"]
                           + [c for _, k, u, _ in ARTIFACTS for c in (k, u)]
                           + COLUMN_ONLY),
        "user_id": f"eq.{uid}", "is_expired": "eq.false", "limit": 5000,
    }
    if not args.all_tiers:
        params["score_tier"] = f"in.({args.tier})"
    rows = httpx.get(f"{url}/rest/v1/jobs", params=params, headers=headers, timeout=120).json()
    if not isinstance(rows, list):
        print(f"query failed: {str(rows)[:300]}")
        return 1
    rows = [r for r in rows if r.get("job_hash")]

    # One listing per prefix beats one HEAD per object: 272 jobs x 2 artifacts
    # is 544 round trips, and the whole bucket prefix is two.
    present: dict[str, set[str]] = {}
    for label, _, _, tmpl in ARTIFACTS:
        prefix = tmpl.format(uid=uid, h="").rsplit("/", 1)[0] + "/"
        keys: set[str] = set()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys |= {o["Key"] for o in page.get("Contents", [])}
        present[label] = keys

    scope = "every tier" if args.all_tiers else f"tiers {args.tier}"
    print(f"\n{len(rows)} active job(s), {scope}\n")

    broken_examples: list[str] = []
    for label, key_col, url_col, tmpl in ARTIFACTS:
        c = Counter()
        no_url = 0
        for r in rows:
            k = bool(r.get(key_col))
            obj = tmpl.format(uid=uid, h=r["job_hash"]) in present[label]
            c[(k, obj)] += 1
            if k and not obj and len(broken_examples) < 6:
                broken_examples.append(f"{label}: {r['job_hash'][:12]} {str(r.get('company'))[:24]}")
            if k and not r.get(url_col):
                no_url += 1
        print(f"  {label.upper()}")
        print(f"    row points at it, object exists    {c[(True, True)]:5}")
        print(f"    row points at it, object MISSING   {c[(True, False)]:5}   <- 403 for the user")
        print(f"    object exists, row does NOT point  {c[(False, True)]:5}   <- generated, unopenable")
        print(f"    neither                            {c[(False, False)]:5}   <- not generated")
        if no_url:
            print(f"    key set but url empty              {no_url:5}   <- nothing to click")
        print()

    print("  COLUMN-ONLY ARTIFACTS (no object store)")
    for col in COLUMN_ONLY:
        filled = sum(1 for r in rows if r.get(col))
        print(f"    {col:22} {filled:5} of {len(rows)}")

    if broken_examples:
        print("\n  broken links, examples:")
        for e in broken_examples:
            print(f"    {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
