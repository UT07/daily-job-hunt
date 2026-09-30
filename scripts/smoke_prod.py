#!/usr/bin/env python
"""Post-deploy smoke test: exercise the REAL system, not a mock of it.

Why this exists
---------------
On 2026-09-29 the repository had 1,686 passing tests and five separate
production defects, every one of which the suite was structurally incapable of
catching, because every one of them mocked the boundary it needed to test:

  1. Unescaped `%` in generated LaTeX broke compilation. No test compiled a
     resume containing a percent sign.
  2. The PDF -> LaTeX upload conversion raised on EVERY call (two parsers,
     incompatible shapes). The tests passed a fixture in the shape the
     renderer wanted, which the real parser never produces.
  3. The tailored .tex S3 key was built from job_id when every object in the
     bucket is keyed by job_hash — a 0% hit rate. Every test mocked S3.
  4. `apiCall` already follows a 202 to completion; the caller polled again
     with a poll_url that is never in the returned value, so every compile
     404'd. The test stubbed both `apiCall` and `pollPipeline` with shapes
     neither real function produces.
  5. A pipeline run compiled 8 of 10 resumes and reported SUCCEEDED.

The common thread is not carelessness. It is that a mock encodes the author's
belief about a boundary, so a test built on one can only ever confirm that
belief. Each check below therefore touches the real thing: the live bucket,
the deployed API, the actual database rows.

Usage
-----
    set -a && . ./.env && set +a
    .venv/bin/python scripts/smoke_prod.py            # all checks
    .venv/bin/python scripts/smoke_prod.py --quick    # skip the compile check

Exit code is non-zero if any check fails, so CI can gate a deploy on it.
Read-only apart from one compile into a dedicated throwaway S3 key.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import traceback

# Load credentials explicitly.
#
# Until 2026-09-29 the suite had none of its own: the FIRST check that ran did
# `import app`, app.py loads .env at import time, and every later check's _db()
# worked off that side effect. Nothing said so. Reordering the checks, skipping
# the importing one, or rewriting it — which is exactly what happened — broke
# every subsequent check with `KeyError: 'SUPABASE_URL'`, an error that reads
# like a missing secret rather than a missing import.
#
# A verification suite whose credentials depend on the order of its own checks
# is not a suite you can trust to tell you the truth about a deploy.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"))
except ImportError:
    pass  # CI passes real environment variables; there is no .env there.

RESULTS: list[tuple[str, bool, str]] = []

# Checks that cannot run without a deployed API URL. Named here rather than
# hardcoded in main() so adding one cannot silently leave it running against
# nothing.
_NEEDS_API = {"api_alive"}


def check(name: str, incident: str):
    """Register a smoke check. `incident` names the failure it would have caught."""
    def deco(fn):
        def run():
            try:
                detail = fn() or "ok"
                RESULTS.append((name, True, detail))
            except AssertionError as e:
                RESULTS.append((name, False, str(e)))
            except Exception as e:
                RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        run.__name__ = fn.__name__
        run._incident = incident
        return run
    return deco


def _db():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _s3():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))


def _bucket() -> str:
    return os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))


# ---------------------------------------------------------------------------

@check("api-alive", "an API that boots but cannot import its own modules")
def api_alive():
    import httpx
    base = os.environ.get("SMOKE_API_URL") or os.environ.get("VITE_API_URL")
    assert base, "set SMOKE_API_URL (or VITE_API_URL) to the deployed API base"
    r = httpx.get(f"{base.rstrip('/')}/api/health", timeout=45)
    assert r.status_code == 200, f"health returned {r.status_code}"
    body = r.json()
    n = body.get("ai_providers", 0)
    assert n > 0, "health reports zero AI providers — the council cannot run"
    return f"{n} providers, resumes={body.get('resumes_loaded')}"


@check("tex-key-resolves", "the .tex S3 key built from job_id, 0% hit rate")
def tex_key_resolves():
    """The key the code builds must find real objects in the real bucket.

    Scoped to jobs that SHOULD have a tailored .tex. The first version of this
    check sampled any row with a resume_s3_key and required a 50% hit rate,
    which meant it reported "10/20 keys resolve (50%)" as a PASS. Half those
    misses were correct behaviour:

      - C-tier jobs point at default_base.pdf and are never tailored, per the
        B=resume-only / A,S=full artifact policy
      - legacy rows carry human-named keys from before the job_hash convention
      - one row has job_hash = NULL

    Measuring a population that is not expected to pass, then setting the bar
    low enough that it does, is a check that can only ever report noise. Scope
    it to rows whose resume_s3_key follows the {job_hash}_tailored convention —
    those are exactly the ones a tailoring run produced — and then require
    nearly all of them.
    """
    db, s3 = _db(), _s3()
    rows = (
        db.table("jobs")
        .select("job_hash,user_id,resume_s3_key")
        .not_.is_("resume_s3_key", "null")
        .not_.is_("job_hash", "null")
        .limit(200)
        .execute()
        .data
    )
    tailored = [r for r in rows
                if r.get("job_hash") and f"{r['job_hash']}_tailored" in (r.get("resume_s3_key") or "")]
    assert tailored, (
        "no rows follow the {job_hash}_tailored key convention — either "
        "nothing has been tailored, or the convention changed and this check "
        "is now measuring nothing"
    )
    hits = 0
    misses = []
    for r in tailored:
        key = f"users/{r['user_id']}/resumes/{r['job_hash']}_tailored.tex"
        try:
            s3.head_object(Bucket=_bucket(), Key=key)
            hits += 1
        except Exception:
            misses.append(r["job_hash"][:12])
    rate = hits / len(tailored)
    assert rate >= 0.95, (
        f"only {hits}/{len(tailored)} tailored .tex keys resolve ({rate:.0%}). "
        f"The Studio cannot open a job whose .tex is missing. "
        f"Missing: {', '.join(misses[:8])}"
    )
    return f"{hits}/{len(tailored)} tailored .tex keys resolve ({rate:.0%})"


@check("base-resume-tailorable", "a PDF upload stored as plain text, silently unusable")
def base_resume_tailorable():
    """Whatever the pipeline would tailor from must actually be LaTeX."""
    sys.path.insert(0, ".")
    from shared.resume_format import describe_why_not_latex, pick_latest_tailorable

    db = _db()
    users = {r["user_id"] for r in db.table("user_resumes").select("user_id").limit(50).execute().data}
    assert users, "no resumes at all"
    bad = []
    for uid in users:
        rows = (db.table("user_resumes").select("*").eq("user_id", uid)
                .order("created_at", desc=True).limit(10).execute().data)
        chosen, skipped = pick_latest_tailorable(rows)
        if chosen is None:
            bad.append(f"{uid[:8]}: no tailorable resume at all")
        elif skipped:
            newest = rows[0].get("tex_content")
            bad.append(f"{uid[:8]}: skipping {skipped} newer upload(s) — "
                       f"{describe_why_not_latex(newest)[:80]}")
    assert not bad, "; ".join(bad)
    return f"{len(users)} user(s), newest resume is tailorable"


@check("latex-escaping", "an unescaped % silently commenting out a closing brace")
def latex_escaping():
    """The characters that have broken compilation must survive escaping."""
    sys.path.insert(0, "lambdas/pipeline")
    from tailor_resume import escape_body_specials

    body = r"Reduced \textbf{MTTR by 35%} at R&D, using C# and 99.9\% uptime."
    doc = ("\\documentclass{article}\n\\newcommand{\\x}[1]{%\n}\n"
           "\\begin{document}\n" + body + "\n\\end{document}")
    out = escape_body_specials(doc)
    tail = out.split(r"\begin{document}")[1]
    assert not re.search(r"(?<!\\)%", tail), f"unescaped % survived: {tail!r}"
    assert r"\\%" not in out, "a already-escaped % was double-escaped"
    assert "\\newcommand{\\x}[1]{%" in out, "preamble line-continuation was mangled"
    return "%, &, # escaped; preamble untouched"


@check("artifact-completeness", "a run that compiled 8 of 10 and reported SUCCEEDED")
def artifact_completeness():
    """Top-tier active jobs must actually have the artifacts they are due."""
    from datetime import datetime, timedelta, timezone
    db = _db()
    cut = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    rows = (db.table("jobs")
            .select("job_id,score_tier,resume_s3_url,is_expired,first_seen,description")
            .in_("score_tier", ["S", "A"]).eq("is_expired", False)
            .gte("first_seen", cut).limit(500).execute().data)

    # A job with no description cannot be tailored — there is nothing to tailor
    # AGAINST — so it can never acquire a resume and would fail this check on
    # every deploy, forever, with no action that could clear it.
    #
    # Measured 2026-09-30: the one row failing this check was
    # d1ad2affe913 (Viatel) — tier A, description length 0, score_status
    # pending, left behind by an Add Job attempt that failed partway on
    # 2026-09-29 when the council gave up (fixed in #141).
    #
    # Excluding them is not lowering the bar: it is scoping the check to the
    # population it is meant to judge. They are counted and reported so the
    # exclusion is visible rather than silent, and _find_or_create_job no
    # longer creates them.
    tailorable = [r for r in rows if (r.get("description") or "").strip()]
    stubs = len(rows) - len(tailorable)

    missing = [r for r in tailorable if not r.get("resume_s3_url")]
    assert not missing, (
        f"{len(missing)} of {len(tailorable)} tailorable active S/A jobs have no "
        "resume. A partial artifact loss looks identical to a clean run from the "
        f"pipeline's status. Missing: "
        f"{', '.join(str(r.get('job_id'))[:12] for r in missing[:6])}"
    )
    note = f" ({stubs} descriptionless stub(s) excluded)" if stubs else ""
    return f"{len(tailorable)} tailorable active S/A jobs, all have a resume{note}"


@check("no-pipeline-states-in-user-column", "the pipeline writing into application_status")
def no_pipeline_states():
    db = _db()
    for bad in ("ready", "scored", "failed"):
        n = (db.table("jobs").select("job_hash", count="exact")
             .eq("application_status", bad).limit(1).execute().count)
        assert n == 0, (
            f"{n} rows hold application_status={bad!r}. That is a pipeline state in "
            "the user's column; the dashboard Status filter cannot match it."
        )
    return "application_status holds only user-facing states"


CHECKS = [api_alive, tex_key_resolves, base_resume_tailorable,
          latex_escaping, artifact_completeness, no_pipeline_states]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip checks that need the deployed API")
    args = ap.parse_args()

    import logging
    logging.disable(logging.INFO)

    print("post-deploy smoke test — real infrastructure, no mocks\n")
    skipped = []
    for fn in CHECKS:
        if args.quick and fn.__name__ in _NEEDS_API:
            skipped.append(fn.__name__)
            print(f"  SKIP  {fn.__name__:30s}    --quick, no API URL configured")
            print(f"        NOT CHECKED: {fn._incident}")
            continue
        t0 = time.time()
        fn()
        name, ok, detail = RESULTS[-1]
        print(f"  {'PASS' if ok else 'FAIL'}  {name:30s} {time.time()-t0:5.1f}s  {detail}")
        if not ok:
            print(f"        would have caught: {fn._incident}")

    failed = [r for r in RESULTS if not r[1]]
    # A skipped check is NOT a passed check. The old summary printed
    # "5/5 passed" for a run where api-alive never executed, which is the same
    # shape as the Step Function reporting SUCCEEDED on a no-op: a status that
    # cannot distinguish "did the work" from "did not do the work".
    total = len(CHECKS)
    line = f"\n  {len(RESULTS) - len(failed)} passed"
    if failed:
        line += f", {len(failed)} FAILED"
    if skipped:
        line += f", {len(skipped)} SKIPPED ({', '.join(skipped)})"
    print(f"{line}  — of {total} checks")
    if skipped:
        print("  Skipped checks verified NOTHING. Set SMOKE_API_URL to run them.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
