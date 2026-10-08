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


def _verdict_column_exists(url, headers) -> bool:
    """One cheap read. If the column is absent the measurement cannot be
    stored, and measuring anyway costs a pdfplumber text extraction per
    document for a result that is discarded — which is what made the first
    live run slow enough to interrupt."""
    r = httpx.get(f"{url}/rest/v1/jobs", params={"select": "resume_verdict", "limit": "1"},
                  headers=headers, timeout=30)
    return r.status_code < 300


def _reconcile(rows, *, url, headers, user_id, commit, verbose,
               relink_always=False, tier_label="", links_only=False) -> dict:
    """Measure and (optionally) write one row per entry. The whole driver.

    `relink_always` is for the post-batch path: a recompile overwrites the PDF
    at the same key, so a row that already HAS the key still needs a fresh
    presigned URL and a fresh verdict. Without it, reconciling after a
    recompile would leave the previous document's verdict standing beside the
    new PDF -- a stale grade is worse than none, because it reads as current.
    """
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    policy = httpx.get(f"{url}/rest/v1/users",
                       params={"select": "composition_policy", "id": f"eq.{user_id}"},
                       headers=headers, timeout=30).json()
    policy = policy[0]["composition_policy"] if isinstance(policy, list) and policy else None
    expected_pages = resolve(policy)["pages"]

    if not links_only and not _verdict_column_exists(url, headers):
        links_only = True
        if verbose:
            print("  ! jobs.resume_verdict is absent, so a verdict cannot be stored.")
            print("    Skipping the measurement rather than computing one to discard it.")
            print("    Links are written either way; apply the migration and re-run for verdicts.")

    quality = None if links_only else _quality_checker()
    base_body, fabrication_baseline = ("", "") if links_only else _base_resume(user_id)
    tmp = Path(tempfile.mkdtemp())

    if verbose:
        print(f"\n{'COMMITTING' if commit else 'DRY RUN -- nothing will be written'}")
        print(f"{len(rows)} row(s){tier_label}\n")

    grades = Counter()
    reasons = Counter()
    linked = skipped = written = failed = 0
    verdict_column_missing = [False]   # a list so the inner patch helper can set it

    for i, row in enumerate(rows, 1):
        h = row["job_hash"]
        pdf_key = f"users/{user_id}/resumes/{h}_tailored.pdf"
        tex_key = f"users/{user_id}/resumes/{h}_tailored.tex"
        if links_only:
            # head_object, not get_object: the question is "does the document
            # exist", and downloading it to answer that is the whole cost.
            try:
                s3.head_object(Bucket=bucket, Key=pdf_key)
            except Exception:
                skipped += 1
                continue
            tex, pdf_path = "", None
        else:
            try:
                pdf_bytes = s3.get_object(Bucket=bucket, Key=pdf_key)["Body"].read()
                tex = s3.get_object(Bucket=bucket, Key=tex_key)["Body"].read().decode()
            except Exception:
                skipped += 1
                continue
            pdf_path = tmp / f"{h}.pdf"
            pdf_path.write_bytes(pdf_bytes)

        # Each of the four, or None where it genuinely could not be taken.
        # In links-only mode NONE of them are taken, and `from_step_results`
        # grades that `unmeasured` — which is the honest answer and is why the
        # verdict is not written at all in that mode.
        page_violations = None if links_only else check_pdf(
            str(pdf_path), expected_pages=expected_pages)
        composition = None if links_only else check_output(tex, policy)
        ats = None if links_only else check_ats_extraction(str(pdf_path), tex)
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

        needs_link = relink_always or not row.get("resume_s3_key")
        if needs_link:
            linked += 1

        if not commit:
            if verbose and not links_only and (i <= 12 or verdict.grade in ("fail", "unmeasured")):
                print(f"  [{i:3}/{len(rows)}] {h[:12]} {verdict.grade:10}"
                      f"{' +link' if needs_link else '':6}  "
                      f"{str(row.get('company') or '')[:20]:20} "
                      f"{('; '.join(verdict.reasons[:2]))[:60]}")
            continue

        update = {} if links_only else {"resume_verdict": verdict.to_row()}
        if needs_link:
            update["resume_s3_key"] = pdf_key
            update["resume_s3_url"] = s3.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": pdf_key},
                ExpiresIn=PRESIGN_SECONDS)
        def _patch(body):
            return httpx.patch(f"{url}/rest/v1/jobs",
                               params={"user_id": f"eq.{user_id}", "job_hash": f"eq.{h}"},
                               headers={**headers, "Content-Type": "application/json",
                                        "Prefer": "return=minimal"},
                               json=body, timeout=60)

        if not update:
            continue
        resp = _patch(update)
        # The verdict column arrives by a migration applied BY HAND, and the
        # link does not depend on it. PostgREST fails the WHOLE patch on an
        # unknown column, so without this retry a missing migration blocks the
        # one thing that actually matters to the user: a résumé they can open.
        # Same degrade save_job already does, for the same reason.
        if resp.status_code >= 300 and (
                "PGRST204" in resp.text or "schema cache" in resp.text.lower()):
            if not verdict_column_missing[0]:
                print("  ! jobs.resume_verdict is absent — writing the link without the "
                      "verdict. Apply supabase/migrations/20261007230000_jobs_resume_verdict.sql "
                      "to store verdicts too; the résumés do not need it.")
                verdict_column_missing[0] = True
            stripped = {k: v for k, v in update.items() if k != "resume_verdict"}
            resp = _patch(stripped) if stripped else resp
        if resp.status_code < 300:
            written += 1
        else:
            failed += 1
            print(f"  [{i}] PATCH {resp.status_code} for {h[:12]}: {resp.text[:160]}")

    summary = {"measured": sum(grades.values()), "written": written, "failed": failed,
               "skipped": skipped, "needs_link": linked, "grades": dict(grades)}

    if verbose and links_only:
        # Deliberately NOT the grade table here. In this mode nothing was
        # measured, so every check reads "unmeasured" and the counts look like
        # findings — "ats 20" reads as twenty ATS problems when it means twenty
        # documents nobody looked at. A number that invites the wrong reading is
        # worse than no number.
        print(f"\nfound {summary['measured']} résumé(s) in S3, "
              f"{skipped} row(s) with no document")
    elif verbose:
        print(f"\nmeasured {summary['measured']} résumé(s), "
              f"skipped {skipped} with no artefact in S3")
        for g in ("pass", "warn", "fail", "unmeasured"):
            if grades[g]:
                print(f"  {g:11} {grades[g]:4}")
        if reasons:
            print("\n  checks contributing a reason:")
            for name, n in reasons.most_common():
                print(f"    {name:14} {n:4}")
    if verbose:
        print(f"\n  rows needing resume_s3_key/url: {linked}")
        if commit:
            print(f"  rows written: {written}, failed: {failed}")
            print("\nVerify the thing that actually matters -- the deploy's own check:")
            print("  SMOKE_API_URL=$(grep -hoE '^VITE_API_URL=.+' web/.env.production | "
                  "cut -d= -f2-) .venv/bin/python scripts/smoke_prod.py")
        else:
            print(f"  rows that WOULD be written: {len(rows) - skipped}")
            print("\nNothing was written. Re-run with --commit.")
    return summary


def reconcile_hashes(job_hashes, *, user_id=None, commit=False, verbose=True) -> dict:
    """Reconcile exactly these job hashes. Same summary `main` prints.

    The entry point `retailor_bulk.py` calls as its final phase, so the bulk
    path completes the same three steps the state machine does -- tailor,
    compile, WRITE THE ROW -- instead of only the first two. Leaving that to a
    printed reminder would be CLAUDE.md #4 again: a reminder is a request, the
    call is the guarantee.
    """
    url, headers = _rb._db()
    user_id = user_id or _rb._one_user(url, headers)
    hashes = [h for h in dict.fromkeys(job_hashes) if h]
    if not hashes:
        return {"measured": 0, "written": 0, "failed": 0, "skipped": 0, "grades": {}}
    rows = [{"job_hash": h, "company": "", "resume_s3_key": None} for h in hashes]
    return _reconcile([*rows], url=url, headers=headers, user_id=user_id,
                      commit=commit, verbose=verbose, relink_always=True,
                      tier_label=" just compiled")


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
    ap.add_argument("--relink", action="store_true",
                    help="refresh key/url even where already set (after a recompile)")
    ap.add_argument("--links-only", action="store_true",
                    help="write resume_s3_key/url and nothing else — no PDF download, "
                         "no measurement. Seconds instead of minutes.")
    args = ap.parse_args()

    url, headers = _rb._db()
    user_id = args.user_id or _rb._one_user(url, headers)

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

    label = f" in tiers {args.tier}" + (" with resume_s3_key NULL" if args.only_missing else "")
    summary = _reconcile(rows, url=url, headers=headers, user_id=user_id,
                         commit=args.commit, verbose=True,
                         relink_always=args.relink, tier_label=label,
                         links_only=args.links_only)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
