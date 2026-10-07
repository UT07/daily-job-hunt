#!/usr/bin/env python
"""Write what save_job would have written, for resumes produced outside it.

WHY THIS EXISTS

`scripts/retailor_bulk.py` invokes `naukribaba-compile-latex` directly. That
produces a PDF in S3 and nothing else -- `save_job` is the only writer of
`jobs.resume_s3_key` and `jobs.resume_s3_url`, and the bulk path never reaches
it. Measured 2026-10-07:

    273 active S/A rows
      PDF in S3 and resume_s3_key set    60
      PDF in S3, resume_s3_key NULL     212   <- the dashboard cannot link it
      no PDF, no key                      0
      resume_s3_key set, no PDF           0

212 resumes existed and the user could not see one of them. The deploy's own
`artifact-completeness` smoke check caught it and failed the deploy, correctly.
An audit written the same day read S3 and reported "missing 0" -- because it
measured the ARTEFACT and not the user's view of it. The user opens a resume
through the dashboard, and the dashboard reads the row. CLAUDE.md #7: scope a
check to the population it is meant to judge.

WHAT IT DOES

For every active S/A job whose PDF exists in S3, it re-derives the same four
measurements production takes, from the stored artefacts, using THE SAME
functions -- not reimplementations:

    pages        shared.page_check.check_pdf
    composition  shared.composition_policy.check_output
    ats          shared.ats_extract_check.check_ats_extraction
    writing      tailor_resume._quality_warnings

grades them with `shared.resume_verdict`, and writes `resume_s3_key`,
`resume_s3_url` and `resume_verdict`.

The base resume comes from `shared.resume_format.fetch_tailorable_resume`,
because that function is the single place the "which resume is the user's" rule
lives -- three modules once held three different answers.

DRY RUN BY DEFAULT. It prints exactly what it would change and the verdict
distribution it measured. `--commit` writes. The dry run does the full
measurement rather than estimating, because a dry run that reports what it did
not measure is the same lie as a status that cannot fail.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas/pipeline"))

import boto3  # noqa: E402
import httpx  # noqa: E402

from shared.ats_extract_check import check_ats_extraction  # noqa: E402
from shared.composition_policy import check_output, resolve  # noqa: E402
from shared.page_check import check_pdf  # noqa: E402
from shared.resume_verdict import from_step_results  # noqa: E402

_spec = importlib.util.spec_from_file_location("_rb", ROOT / "scripts/retailor_bulk.py")
_rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rb)

PRESIGN_SECONDS = 2592000   # 30 days, matching save_job exactly


def _quality_checker():
    """tailor_resume's writing rubric, or None if it cannot be imported.

    Imported lazily and reported rather than swallowed: if the rubric is
    unavailable the writing check is UNMEASURED, which grades every row
    `unmeasured`. That is the honest outcome and it must be visible, not
    silently downgraded to an empty list -- which would read as "checked and
    clean" for a check that never ran.
    """
    try:
        import tailor_resume
        return tailor_resume._quality_warnings
    except Exception as exc:  # noqa: BLE001
        print(f"  ! writing rubric unavailable ({type(exc).__name__}: {exc});")
        print("    every verdict will be `unmeasured` on that check")
        return None


def _base_resume(user_id: str):
    """(base_body, fabrication_baseline) via production's own selection rule."""
    from shared.resume_format import fetch_tailorable_resume
    from supabase import create_client
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    base = fetch_tailorable_resume(db, user_id)
    tex = base.newest_tex or ""
    body = tex.split(r"\begin{document}", 1)[-1] if r"\begin{document}" in tex else tex
    return body, (base.all_tex or body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true",
                    help="actually write the rows (default: measure and report only)")
    ap.add_argument("--tier", default="S,A")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--max", type=int, default=None, dest="max_jobs")
    ap.add_argument("--only-missing", action="store_true",
                    help="only rows whose resume_s3_key is NULL (the 212 case)")
    args = ap.parse_args()

    url, headers = _rb._db()
    user_id = args.user_id or _rb._one_user(url, headers)
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    policy = httpx.get(f"{url}/rest/v1/users",
                       params={"select": "composition_policy", "id": f"eq.{user_id}"},
                       headers=headers, timeout=30).json()
    policy = policy[0]["composition_policy"] if isinstance(policy, list) and policy else None
    expected_pages = resolve(policy)["pages"]

    rows = httpx.get(f"{url}/rest/v1/jobs", params={
        "select": "job_hash,title,company,score_tier,resume_s3_key",
        "user_id": f"eq.{user_id}", "is_expired": "eq.false",
        "score_tier": f"in.({args.tier})", "limit": 2000,
    }, headers=headers, timeout=60).json()
    if not isinstance(rows, list):
        print(f"query failed: {str(rows)[:300]}")
        return 1

    rows = [r for r in rows if r.get("job_hash")]
    if args.only_missing:
        rows = [r for r in rows if not r.get("resume_s3_key")]
    if args.max_jobs:
        rows = rows[:args.max_jobs]

    quality = _quality_checker()
    base_body, fabrication_baseline = _base_resume(user_id)
    tmp = Path(tempfile.mkdtemp())

    print(f"\n{'DRY RUN -- nothing will be written' if not args.commit else 'COMMITTING'}")
    print(f"{len(rows)} row(s) in tiers {args.tier}"
          f"{' with resume_s3_key NULL' if args.only_missing else ''}\n")

    grades = Counter()
    linked = skipped = written = failed = 0
    reasons = Counter()

    for i, row in enumerate(rows, 1):
        h = row["job_hash"]
        pdf_key = f"users/{user_id}/resumes/{h}_tailored.pdf"
        tex_key = f"users/{user_id}/resumes/{h}_tailored.tex"
        try:
            pdf_bytes = s3.get_object(Bucket=bucket, Key=pdf_key)["Body"].read()
            tex = s3.get_object(Bucket=bucket, Key=tex_key)["Body"].read().decode()
        except Exception:
            skipped += 1
            continue

        pdf_path = tmp / f"{h}.pdf"
        pdf_path.write_bytes(pdf_bytes)

        # Each of the four, or None where it genuinely could not be taken.
        page_violations = check_pdf(str(pdf_path), expected_pages=expected_pages)
        composition = check_output(tex, policy)
        ats = check_ats_extraction(str(pdf_path), tex)
        writing = None
        if quality is not None:
            body = tex.split(r"\begin{document}", 1)[-1] if r"\begin{document}" in tex else tex
            try:
                writing = quality(body, base_body, fabrication_baseline)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}] writing check raised for {h[:12]}: {exc}")

        verdict = from_step_results(
            {"composition_violations": composition, "quality_warnings": writing},
            {"page_violations": page_violations, "ats_violations": ats},
        )
        grades[verdict.grade] += 1
        for r in verdict.reasons:
            reasons[r.split(":", 1)[0]] += 1

        needs_link = not row.get("resume_s3_key")
        if needs_link:
            linked += 1

        if not args.commit:
            if i <= 12 or verdict.grade in ("fail", "unmeasured"):
                flag = " +link" if needs_link else ""
                print(f"  [{i:3}/{len(rows)}] {h[:12]} {verdict.grade:10}{flag}  "
                      f"{str(row.get('company'))[:20]:20} "
                      f"{('; '.join(verdict.reasons[:2]))[:60]}")
            continue

        update = {"resume_verdict": verdict.to_row()}
        if needs_link:
            update["resume_s3_key"] = pdf_key
            update["resume_s3_url"] = s3.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": pdf_key},
                ExpiresIn=PRESIGN_SECONDS)
        resp = httpx.patch(f"{url}/rest/v1/jobs",
                           params={"user_id": f"eq.{user_id}", "job_hash": f"eq.{h}"},
                           headers={**headers, "Content-Type": "application/json",
                                    "Prefer": "return=minimal"},
                           json=update, timeout=60)
        if resp.status_code < 300:
            written += 1
        else:
            failed += 1
            print(f"  [{i}] PATCH {resp.status_code} for {h[:12]}: {resp.text[:160]}")
            if "PGRST204" in resp.text or "schema cache" in resp.text.lower():
                print("      jobs.resume_verdict is absent — apply "
                      "supabase/migrations/20261007230000_jobs_resume_verdict.sql")
                return 1

    print(f"\nmeasured {sum(grades.values())} resume(s), skipped {skipped} with no artefact in S3")
    for g in ("pass", "warn", "fail", "unmeasured"):
        if grades[g]:
            print(f"  {g:11} {grades[g]:4}")
    if reasons:
        print("\n  checks contributing a reason:")
        for name, n in reasons.most_common():
            print(f"    {name:14} {n:4}")
    print(f"\n  rows needing resume_s3_key/url: {linked}")
    if args.commit:
        print(f"  rows written: {written}, failed: {failed}")
        print("\nVerify the thing that actually matters -- re-run the deploy's own check:")
        print("  SMOKE_API_URL=$(grep -hoE '^VITE_API_URL=.+' web/.env.production | "
              "cut -d= -f2-) .venv/bin/python scripts/smoke_prod.py")
    else:
        print(f"  rows that WOULD be written: {len(rows) - skipped}")
        print("\nNothing was written. Re-run with --commit.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
