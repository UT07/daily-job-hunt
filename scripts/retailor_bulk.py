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
# scripts/ too, so the reconcile phase can import its sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

FUNCTION = "naukribaba-tailor-resume"
# Tailoring writes a .tex and NOTHING ELSE. The PDF is produced by the state
# machine's CompileResume state (template.yaml), which invokes this function --
# so a script that invokes the tailor directly leaves the previous PDF in place.
#
# Measured 2026-10-06 on job 0ab484ad7687:
#     .tex  modified 2026-09-30 19:56Z   <- the composition fixes
#     .pdf  modified 2026-09-30 07:23Z   <- 12.5 hours OLDER
#
# Every batch this script has ever run updated the source and left the document
# the user actually opens untouched. That is why none of the composition work
# appeared to land. The page fitting and separator normalisation also live in
# compile, so they can ONLY reach a PDF through this.
COMPILE_FUNCTION = "naukribaba-compile-latex"
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


def partition_unhashed(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split rows into (tailorable, unhashed). Pure, so it is testable directly.

    A NULL job_hash cannot go into PostgREST's `in.(...)` filter -- `,`.join
    raises TypeError on it -- and there is nothing for handler() to look up
    anyway. Manually-added jobs have produced these: job_id and canonical_hash
    set, job_hash NULL (one A-tier row, 2026-09-29, which also had an empty
    description and no jobs_raw row at all).
    """
    return ([j for j in jobs if j.get("job_hash")],
            [j for j in jobs if not j.get("job_hash")])


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

    # Reported, never dropped quietly: a batch that silently narrows its own
    # population is the same lie as a status that cannot fail.
    jobs, unhashed = partition_unhashed(jobs)
    for j in unhashed:
        print(f"  {j.get('score_tier')} SKIPPED, no job_hash to look up: "
              f"{str(j.get('title'))[:40]} @ {j.get('company')}")

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


def tex_key(user_id: str, job_hash: str) -> str:
    """Where tailoring writes, and where compiling reads. Deterministic, so the
    compile phase needs no result from the tailor phase."""
    return f"users/{user_id}/resumes/{job_hash}_tailored.tex"


def wait_for_tex(s3, bucket: str, keys: list[str], newer_than, timeout: float = 600.0):
    """Block until each .tex has been rewritten, or the timeout expires.

    Polls S3 LastModified rather than sleeping a guessed interval: tailoring is
    60-90s on a warm Lambda and several minutes on a cold one, and compiling a
    .tex the tailor has not written yet would silently rebuild the OLD document
    while reporting success.

    Returns (fresh, stale) so the caller can say which jobs it is compiling and
    which it is skipping, instead of reporting a number that covers both.
    """
    import time
    deadline = time.monotonic() + timeout
    pending, fresh = dict.fromkeys(keys), []
    while pending and time.monotonic() < deadline:
        for key in list(pending):
            try:
                if s3.head_object(Bucket=bucket, Key=key)["LastModified"] > newer_than:
                    fresh.append(key)
                    del pending[key]
            except Exception:
                pass            # not written yet, or never existed
        if pending:
            time.sleep(10)
    return fresh, list(pending)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="S,A", help="comma-separated tiers (default S,A)")
    ap.add_argument("--max", type=int, default=None, dest="max_jobs")
    ap.add_argument("--commit", action="store_true", help="actually invoke (default dry run)")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--depth", default="moderate", choices=["light", "moderate", "full"])
    ap.add_argument("--missing-only", action="store_true",
                    help="only jobs with no resume_s3_url yet. After a backlog "
                         "score run most S/A jobs have never been tailored, and "
                         "re-tailoring the ones that have is both the expensive "
                         "half and a fresh council roll that can make a good "
                         "document worse")
    ap.add_argument("--compile-only", action="store_true",
                    help="skip tailoring; just recompile the existing .tex. No AI "
                         "calls, and it is what fixes a PDF that is older than "
                         "its own source")
    ap.add_argument("--only-hashes", default=None, metavar="FILE",
                    help="re-tailor only the job_hashes listed in FILE "
                         "(whitespace-separated). Unmatched hashes are reported.")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the row update (the PDFs will exist but the "
                         "dashboard cannot link them)")
    ap.add_argument("--no-compile", action="store_true",
                    help="tailor without recompiling. Leaves the PDF stale; only "
                         "useful when a compile is being driven separately")
    ap.add_argument("--stagger", type=float, default=6.0,
                    help="seconds between invocations; the council shares a Groq "
                         "TPM ceiling, so firing 30 at once mostly produces 429s")
    args = ap.parse_args()

    url, headers = _db()
    user_id = args.user_id or _one_user(url, headers)
    tiers = [t.strip().upper() for t in args.tier.split(",") if t.strip()]

    jobs = select_jobs(url, headers, user_id, tiers, args.max_jobs)
    if args.only_hashes:
        wanted = {h.strip() for h in Path(args.only_hashes).read_text().split() if h.strip()}
        before = len(jobs)
        jobs = [j for j in jobs if j.get("job_hash") in wanted]
        # Reported, never silent: a hash in the file that matched no job is a
        # job this run will NOT touch, and a caller who asked for 134 and got
        # 97 needs to know which 37 are missing rather than reading a success.
        unmatched = wanted - {j.get("job_hash") for j in jobs}
        print(f"--only-hashes: {len(jobs)} of {before} job(s) selected from "
              f"{len(wanted)} hash(es)")
        if unmatched:
            print(f"  {len(unmatched)} hash(es) matched NO job in tier(s) "
                  f"{','.join(tiers)}: {', '.join(sorted(h[:12] for h in unmatched)[:8])}")
    if args.missing_only:
        before = len(jobs)
        jobs = [j for j in jobs if not (j.get("resume_s3_url") or "").strip()]
        print(f"--missing-only: {before - len(jobs)} job(s) already have a "
              f"resume and are left alone")
    print(f"{len(jobs)} job(s) in tier(s) {','.join(tiers)} for user {user_id[:8]}")
    for j in jobs[:10]:
        print(f"  {j['score_tier']}  {j['job_hash'][:12]}  "
              f"{str(j['title'])[:38]:40s} {str(j['company'])[:22]}")
    if len(jobs) > 10:
        print(f"  ... and {len(jobs) - 10} more")

    if not args.commit:
        print("\nDRY RUN — nothing invoked. Re-run with --commit.")
        return 0
    if not jobs:
        return 0

    import boto3
    import datetime

    lam = boto3.client("lambda", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")
    started = datetime.datetime.now(datetime.timezone.utc)
    keys = [tex_key(user_id, j["job_hash"]) for j in jobs]

    sent = failed = 0
    if args.compile_only:
        print("\n--compile-only: skipping tailoring, recompiling the stored .tex")
        fresh, stale = keys, []
    else:
        for i, j in enumerate(jobs, 1):
            try:
                # InvocationType="Event": returns 202 at once. A synchronous
                # invoke times out at 60s on a 60-90s job and reports failure
                # for work that succeeds.
                resp = lam.invoke(
                    FunctionName=FUNCTION, InvocationType="Event",
                    Payload=json.dumps({"job_hash": j["job_hash"],
                                        "user_id": user_id,
                                        "tailoring_depth": args.depth}).encode(),
                )
                code = resp.get("StatusCode")
                if code == 202:
                    sent += 1
                    print(f"  [{i}/{len(jobs)}] tailor queued {j['job_hash'][:12]} "
                          f"{str(j['title'])[:34]}", flush=True)
                else:
                    failed += 1
                    print(f"  [{i}/{len(jobs)}] UNEXPECTED StatusCode {code} "
                          f"for {j['job_hash'][:12]}", flush=True)
            except Exception as exc:  # noqa: BLE001 — one bad job must not stop the batch
                failed += 1
                print(f"  [{i}/{len(jobs)}] FAILED to queue {j['job_hash'][:12]}: {exc}",
                      flush=True)
            if i < len(jobs):
                time.sleep(args.stagger)

        print(f"\ntailor: queued {sent}, failed {failed}")
        if args.no_compile:
            print("--no-compile: the PDFs still show the PREVIOUS document.")
            return 0 if failed == 0 else 1

        print("waiting for each .tex to be rewritten before compiling...", flush=True)
        fresh, stale = wait_for_tex(s3, bucket, keys, started)
        print(f"  {len(fresh)} rewritten, {len(stale)} not", flush=True)
        for key in stale:
            print(f"  SKIP compile, .tex never rewritten: {key.rsplit('/', 1)[-1]}")

    # --- compile ----------------------------------------------------------
    # The phase this script never had. Tailoring writes a .tex and nothing
    # else; the PDF comes from the state machine's CompileResume state. Driving
    # the tailor directly therefore produced a new source beside a stale
    # document -- and page fitting and separator normalisation live in compile,
    # so they can reach a PDF no other way.
    compiled = compile_failed = 0
    for i, key in enumerate(fresh, 1):
        job_hash = key.rsplit("/", 1)[-1].replace("_tailored.tex", "")
        try:
            resp = lam.invoke(
                FunctionName=COMPILE_FUNCTION, InvocationType="Event",
                Payload=json.dumps({"tex_s3_key": key, "job_hash": job_hash,
                                    "user_id": user_id, "doc_type": "resume"}).encode(),
            )
            if resp.get("StatusCode") == 202:
                compiled += 1
                print(f"  [{i}/{len(fresh)}] compile queued {job_hash[:12]}", flush=True)
            else:
                compile_failed += 1
                print(f"  [{i}/{len(fresh)}] compile UNEXPECTED {resp.get('StatusCode')} "
                      f"{job_hash[:12]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            compile_failed += 1
            print(f"  [{i}/{len(fresh)}] compile FAILED {job_hash[:12]}: {exc}", flush=True)
        if i < len(fresh):
            time.sleep(min(args.stagger, 8.0))   # compiling is ~15s, not 90s

    print(f"\ncompile: queued {compiled}, failed {compile_failed}")

    # --- reconcile ---------------------------------------------------------
    # The phase this script was missing until 2026-10-08, and the reason it
    # mattered. Tailoring writes a .tex, compiling writes a PDF, and NEITHER
    # writes the row: `save_job` is the only writer of jobs.resume_s3_key and
    # jobs.resume_s3_url, and this script invokes the two Lambdas directly
    # without ever reaching it. Measured the day it was found:
    #
    #     273 active S/A rows
    #       PDF in S3 and resume_s3_key set    60
    #       PDF in S3, resume_s3_key NULL     212   <- invisible in the dashboard
    #
    # 212 résumés existed and the user could not open one of them. The deploy's
    # artifact-completeness smoke check caught it; an audit that read S3 the
    # same day reported "missing 0", because it measured the artefact and not
    # the user's view of it (CLAUDE.md #7).
    #
    # A printed reminder to go and run the reconcile would be #4 — a request,
    # not a guarantee. So it runs here, and it waits for each PDF first,
    # because reconciling a PDF the compile has not written yet grades the
    # PREVIOUS document and stores that grade as current.
    if args.no_reconcile:
        print("\n--no-reconcile: rows NOT updated. The PDFs exist in S3 and the")
        print("dashboard cannot link them until you run:")
        print("  .venv/bin/python scripts/reconcile_resume_rows.py --relink --commit")
        return 0 if (failed == 0 and compile_failed == 0) else 1

    pdf_keys = [k.replace("_tailored.tex", "_tailored.pdf") for k in fresh]
    print(f"\nwaiting for {len(pdf_keys)} PDF(s) to be written before reconciling...",
          flush=True)
    fresh_pdfs, stale_pdfs = wait_for_tex(s3, bucket, pdf_keys, started, timeout=900.0)
    for key in stale_pdfs:
        print(f"  SKIP reconcile, PDF never written: {key.rsplit('/', 1)[-1]}")

    from reconcile_resume_rows import reconcile_hashes
    hashes = [k.rsplit("/", 1)[-1].replace("_tailored.pdf", "") for k in fresh_pdfs]
    summary = reconcile_hashes(hashes, user_id=user_id, commit=True)

    print("\nVerify the thing that actually matters — the deploy's own check:")
    print("  SMOKE_API_URL=$(grep -hoE '^VITE_API_URL=.+' web/.env.production | "
          "cut -d= -f2-) .venv/bin/python scripts/smoke_prod.py")
    return 0 if (failed == 0 and compile_failed == 0
                 and summary["failed"] == 0 and not stale_pdfs) else 1


if __name__ == "__main__":
    sys.exit(main())
