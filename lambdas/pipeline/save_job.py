import logging
import os
import re

import boto3

from ai_helper import get_supabase
from shared.job_hash_filter import is_job_hash
from shared.resume_verdict import from_step_results

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# compile_latex returns this error_type when the tectonic binary is absent
# (local dev / unit tests). Treated as "no compile attempted", not a failure.
_LOCAL_DEV_ERROR = "tectonic_not_available"

# Truncate stored failure_reason so noisy stderr can't blow up the row.
_FAILURE_REASON_MAX = 500


def _missing_column_name(exc) -> str | None:
    """The column a PostgREST/Postgres 'no such column' error names, if any.

    Same two wordings score_batch handles: PostgREST answers PGRST204 with
    "Could not find the 'x' column ... in the schema cache", while raw Postgres
    says column "x" does not exist. Matching only one of them is what let a
    missing column take down a whole run before.
    """
    text = str(exc)
    m = re.search(r"Could not find the '([^']+)' column", text)
    if m:
        return m.group(1)
    m = re.search(r'column "([^"]+)"(?: of relation "[^"]+")? does not exist', text)
    return m.group(1) if m else None


# Job hashes are lowercase hex. `or=` is a filter-expression surface, so the
# hash is validated before it is interpolated into one: shared.job_hash_filter,
# the one rule score_batch uses too.


def update_job_row(db, user_id: str, job_hash: str, update: dict) -> int:
    """UPDATE the user's row for this job; return how many rows it actually hit.

    Matches `job_hash` OR `canonical_hash`. Rows created by
    app._find_or_create_job (Add Job) carry the hash in canonical_hash with
    job_hash NULL — jobs.job_hash has a foreign key to jobs_raw that a manually
    added job cannot satisfy (see score_batch._write_job_row). Matching
    job_hash alone selected ZERO rows for every such job, PostgREST answered
    success either way, and the callers reported saved: True for a write that
    went nowhere — the same shape reconcile_resume_rows.py was fixed for on
    2026-10-08 (b3026c307c74: PDF in S3, resume_s3_key still NULL).

    The count is read from the returned rows (supabase-py returns the updated
    representation). Anything that is not a list of rows is counted as zero:
    "written" must not mean "asked" (CLAUDE.md #2). A non-hex hash is not
    interpolated into `or=`; it is matched on job_hash alone, by parameter.
    """
    q = db.table("jobs").update(update).eq("user_id", user_id)
    if is_job_hash(job_hash):
        q = q.or_(f"job_hash.eq.{job_hash},canonical_hash.eq.{job_hash}")
    else:
        logger.warning("[save_job] %r is not a hex job hash — matching job_hash only",
                       job_hash)
        q = q.eq("job_hash", job_hash)
    data = getattr(q.execute(), "data", None)
    return len(data) if isinstance(data, list) else 0


def _compile_failure_reason(compile_result):
    """Extract a human-readable failure reason from a compile_latex error dict.

    Returns None when the result is healthy or only flags the local-dev case.
    """
    if not compile_result:
        return None
    if compile_result.get("pdf_s3_key"):
        return None
    error_type = compile_result.get("error")
    if not error_type or error_type == _LOCAL_DEV_ERROR:
        return None
    detail = compile_result.get("stderr") or ""
    reason = f"{error_type}: {detail}" if detail else error_type
    return reason[:_FAILURE_REASON_MAX]


def handler(event, context):
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")

    # Extract PDF keys from accumulated step results — may not exist if upstream steps failed
    resume_pdf_key = None
    cover_letter_pdf_key = None

    if "compile_result" in event:
        resume_pdf_key = event["compile_result"].get("pdf_s3_key")
    elif "resume_pdf_s3_key" in event:
        resume_pdf_key = event["resume_pdf_s3_key"]

    if "cover_compile_result" in event:
        cover_letter_pdf_key = event["cover_compile_result"].get("pdf_s3_key")
    elif "cover_letter_pdf_s3_key" in event:
        cover_letter_pdf_key = event["cover_letter_pdf_s3_key"]

    resume_failure_reason = _compile_failure_reason(event.get("compile_result"))
    cover_failure_reason = _compile_failure_reason(event.get("cover_compile_result"))

    s3 = boto3.client("s3")
    db = get_supabase()
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    update = {}

    if resume_pdf_key:
        resume_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": resume_pdf_key},
            ExpiresIn=2592000,
        )
        update["resume_s3_url"] = resume_url
        update["resume_s3_key"] = resume_pdf_key

    if cover_letter_pdf_key:
        cl_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": cover_letter_pdf_key},
            ExpiresIn=2592000,
        )
        update["cover_letter_s3_url"] = cl_url
        # Persist the KEY too, exactly as the resume branch above does.
        # _refresh_s3_urls re-signs from cover_letter_s3_key; without it the
        # presigned URL simply expires after 7 days and nothing can ever mint a
        # new one, so the dashboard keeps showing a cover-letter button that
        # 403s. Measured 2026-09-29: 400 jobs had a cover_letter_s3_url and the
        # key column did not exist at all.
        update["cover_letter_s3_key"] = cover_letter_pdf_key

    # application_status is the USER's column — what they did with the job
    # (New / Applied / Interview / ...). This function used to write "failed",
    # "ready" and "scored" into it, which are pipeline states, not user
    # actions. Two consequences, both live on 2026-09-28:
    #
    #   1. Those values are not in app.py's _VALID_STATUSES, so the API would
    #      400 a user setting them while the pipeline wrote them freely, and
    #      the dashboard's Status filter could never match the ~68% of rows
    #      holding one.
    #   2. Writing one ERASED a real user status. A job the user marked
    #      "Applied" reverted to "ready" on the next pipeline run.
    #
    # All three were already derivable, so nothing is lost by not writing them:
    #   failed -> failure_reason IS NOT NULL
    #   ready  -> resume_s3_url IS NOT NULL
    #   scored -> match_score IS NOT NULL
    # One stored answer to "is this resume any good", aggregated from the four
    # measurements the pipeline already takes. See shared/resume_verdict.py for
    # the grading rules and why an unmeasured check is graded `unmeasured`
    # rather than `pass`.
    #
    # Written ONLY when a compile actually ran, because the verdict grades a
    # DOCUMENT: no compile, no document, nothing to grade. Two cases this
    # deliberately excludes, and the first was caught by an existing test
    # rather than by reasoning, which is the better way round:
    #
    #   * _LOCAL_DEV_ERROR -- tectonic absent, so the compile was never
    #     attempted. Already "nothing to record and nothing to write" here;
    #     grading it would make every local dry-run PATCH a row.
    #   * no compile_result at all -- a cover-letter-only pass, or a
    #     SaveJobAfterError that never reached tailoring. Writing `unmeasured`
    #     there would ERASE a verdict that was true: the application_status
    #     mistake again, a pipeline state overwriting real user-visible data.
    #
    # A compile that ran and failed IS graded, as `unmeasured` -- it measured
    # nothing, and saying so next to failure_reason beats leaving the last
    # successful run's grade standing.
    compile_result = event.get("compile_result")
    if compile_result and compile_result.get("error") != _LOCAL_DEV_ERROR:
        verdict = from_step_results(event.get("tailor_result"), compile_result)
        row = verdict.to_row()
        # Recorded beside the grade, deliberately NOT inside `checks`: these
        # are the block-severity violations the council finalized best-effort
        # with, and they do not grade the document until their false-positive
        # rate at this position has been measured (CLAUDE.md #16). Keeping
        # them out of `checks` also keeps the gate's signal intact -- a 5th
        # required check that is absent on every legacy-engine and backfilled
        # row would grade the entire corpus `unmeasured`.
        guard_violations = (event.get("tailor_result") or {}).get("guard_violations")
        if guard_violations is not None:
            row["guard_violations"] = list(guard_violations)
        # What tailor_resume cut out of the shipped body because the
        # fabrication detector flagged it (tailor_resume._recover_by_stripping).
        # Beside the grade for the same reason as guard_violations: the grade
        # already reflects it -- the strip is an advisory `writing` finding, so
        # the document grades `warn`, not `pass` -- and this is the countable
        # record of exactly which claims were removed. No new column: it lives
        # inside the resume_verdict JSON.
        stripped = (event.get("tailor_result") or {}).get("fabrications_stripped")
        if stripped is not None:
            row["fabrications_stripped"] = list(stripped)
        update["resume_verdict"] = row
        log = logger.warning if verdict.grade in ("fail", "unmeasured") else logger.info
        log("[save_job] %s resume verdict: %s%s", job_hash, verdict.grade,
            f" — {'; '.join(verdict.reasons[:4])}" if verdict.reasons else "")

    if resume_failure_reason:
        update["failure_reason"] = resume_failure_reason
        logger.error(f"[save_job] {job_hash} compile failed: {resume_failure_reason}")
    elif resume_pdf_key:
        # Clear any prior failure on a successful re-run.
        update["failure_reason"] = None

    if cover_failure_reason:
        logger.warning(f"[save_job] {job_hash} cover-letter compile failed (non-fatal): {cover_failure_reason}")

    if not update:
        # Previously this branch was avoided by writing application_status
        # "scored" purely so the dict was non-empty. Nothing to say is a valid
        # outcome; an empty PATCH is not.
        # Same shape as the normal return — a caller should not have to know
        # which branch ran to read the result.
        logger.info(f"[save_job] {job_hash}: nothing to update")
        return {
            "job_hash": job_hash,
            "user_id": user_id,
            "saved": False,
            "has_resume": False,
            "failed": False,
        }

    try:
        matched = update_job_row(db, user_id, job_hash, update)
    except Exception as e:
        # The cover_letter_s3_key column arrives via a migration applied BY HAND
        # — no workflow runs migrations. If this code ships first, a plain
        # UPDATE naming an absent column fails outright and the row keeps
        # NOTHING: the same PGRST204 shape that cost a whole run's writes on
        # 2026-09-28. Drop the one column the error names and write the rest,
        # rather than losing a compiled resume over a column that only affects
        # link refresh.
        col = _missing_column_name(e)
        if not col or col not in update:
            raise
        update.pop(col)
        logger.warning(
            "[save_job] %s: column %r is absent — writing without it. "
            "Apply the migration that adds it.", job_hash, col,
        )
        matched = update_job_row(db, user_id, job_hash, update)

    if not matched:
        # The PDF exists and no row points at it: the dashboard shows nothing.
        # Reported, not raised -- the artifacts are safely in S3 and
        # scripts/reconcile_resume_rows.py can relink them -- but never as saved.
        logger.error("[save_job] %s: the update matched NO jobs row for user %s "
                     "(by job_hash or canonical_hash) — nothing was saved",
                     job_hash, user_id)
    else:
        logger.info(f"[save_job] Updated {job_hash} with {len(update)} fields: {sorted(update)}")
    return {
        "job_hash": job_hash,
        "user_id": user_id,
        "saved": bool(matched),
        "has_resume": bool(resume_pdf_key),
        "failed": bool(resume_failure_reason),
    }
