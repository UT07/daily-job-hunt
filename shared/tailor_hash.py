"""Which hash a job is tailored under. One definition for every caller.

Scraped rows carry `job_hash` (the jobs_raw key) and usually `canonical_hash`
too. Rows created by the API's `_find_or_create_job` carry only
`canonical_hash`: `jobs.job_hash` has a foreign key to jobs_raw that a manual
job cannot satisfy when it is created, so it stays NULL, and
/api/pipeline/run-single upserts jobs_raw under the canonical hash instead.

The single-job pipeline is started with this value as `job_hash`, and
tailor_resume.py writes the tailored .tex to
`users/{user_id}/resumes/{job_hash}_tailored.tex` -- so the Studio's S3 key
must be built from the same value, or it opens a document that is not there.

Every caller used to spell this precedence inline, and they drifted: the bulk
re-tailor read `job["job_hash"][:12]` (TypeError on every manual row) and the
Studio key fell back to job_id. CLAUDE.md #10.

scripts/retailor_bulk.py carries an identical `resolve_tailor_hash`;
tests/unit/test_tailor_hash_resolver.py pins the two to the same answers until
the script imports this one.
"""
from __future__ import annotations


def resolve_tailor_hash(job: dict) -> str | None:
    """The hash to tailor this row under, or None if it has no usable one.

    job_hash wins when present: scraped rows have both, and jobs_raw is keyed
    by job_hash for those. canonical_hash is the FALLBACK, not an override.
    """
    return (job or {}).get("job_hash") or (job or {}).get("canonical_hash") or None
