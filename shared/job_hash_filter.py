"""Is this string safe to put inside a PostgREST `or=` expression as a job hash?

`or=` is a small query language: `,` separates conditions and `.` separates
column, operator and value. Interpolating an unchecked value into
`job_hash.eq.{h},canonical_hash.eq.{h}` lets a value like
`000000,user_id.eq.<id>` add a condition of its own, which matches every row
that user has -- so a score or an artifact link lands on the wrong job.

Job hashes are lowercase hex (`utils.canonical_hash` emits 12 characters;
older paths used longer digests), so the check is a whitelist, not an escape
table: anything that is not 6-64 lowercase hex characters is not a job hash.

One rule, one place. save_job, score_batch and
scripts/reconcile_resume_rows.py each needed it; before 2026-10-09 two of the
three had it (CLAUDE.md #10).
"""
import re

JOB_HASH_RE = re.compile(r"[0-9a-f]{6,64}")


def is_job_hash(value) -> bool:
    """True only for a string of 6-64 lowercase hex characters."""
    return isinstance(value, str) and JOB_HASH_RE.fullmatch(value) is not None
