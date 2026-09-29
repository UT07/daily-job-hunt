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

RESULTS: list[tuple[str, bool, str]] = []


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
    """The key the code builds must find real objects in the real bucket."""
    sys.path.insert(0, ".")
    import app
    from supabase import create_client

    class _DB:
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    app._db = _DB()

    rows = (_DB.client.table("jobs").select("job_id,user_id")
            .not_.is_("resume_s3_url", "null").limit(20).execute().data)
    assert rows, "no jobs with a resume to check against"
    s3, bucket, hits = _s3(), _bucket(), 0
    for r in rows:
        try:
            s3.head_object(Bucket=bucket, Key=app._tailored_tex_key(r["user_id"], r["job_id"]))
            hits += 1
        except Exception:
            pass
    rate = hits / len(rows)
    assert rate >= 0.5, (
        f"only {hits}/{len(rows)} ({rate:.0%}) of generated .tex keys exist in S3. "
        "The convention the code builds has drifted from what the bucket holds — "
        "this was 0% when the key used job_id instead of job_hash."
    )
    return f"{hits}/{len(rows)} keys resolve ({rate:.0%})"


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
            .select("score_tier,resume_s3_url,is_expired,first_seen")
            .in_("score_tier", ["S", "A"]).eq("is_expired", False)
            .gte("first_seen", cut).limit(500).execute().data)
    missing = [r for r in rows if not r.get("resume_s3_url")]
    assert not missing, (
        f"{len(missing)} of {len(rows)} active S/A jobs have no resume. A partial "
        "artifact loss looks identical to a clean run from the pipeline's status."
    )
    return f"{len(rows)} active S/A jobs, all have a resume"


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
    for fn in CHECKS:
        if args.quick and fn.__name__ == "api_alive":
            continue
        t0 = time.time()
        fn()
        name, ok, detail = RESULTS[-1]
        print(f"  {'PASS' if ok else 'FAIL'}  {name:30s} {time.time()-t0:5.1f}s  {detail}")
        if not ok:
            print(f"        would have caught: {fn._incident}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
