"""Which jobs_raw fields can be trusted for a user who did not write them.

`jobs_raw` is SHARED: no user_id, keyed by
canonical_hash(company, title, description). A row's apply_url and location
are NOT covered by that hash. For a scraped row they are the scraper's, read
from the job board. For a MANUAL row (source='manual') they were whatever the
first user to paste that JD typed -- an attacker's phishing link, or a
location chosen to move the geo / work-auth score cap.

Since 2026-10-09 the API no longer writes them on manual rows, but rows
written before that still carry them (a migration that blanks them exists,
supabase/migrations/20261009120000_jobs_raw_manual_rows_drop_submitter_fields.sql,
and is applied separately). So every reader that hands a jobs_raw row's
apply_url or location to another user goes through here, and must be safe
whether or not that migration has run.
"""

# Supplied by a submitter, not covered by the hash.
SUBMITTER_FIELDS = ("apply_url", "location")


def strip_untrusted_raw_fields(row: dict) -> dict:
    """A copy of a jobs_raw `row` without submitter-supplied fields.

    Scraped rows are returned unchanged (as a copy). A manual row loses
    apply_url and location, which become None -- "unknown", the value every
    reader already handles.
    """
    out = dict(row or {})
    if (out.get("source") or "") == "manual":
        for field in SUBMITTER_FIELDS:
            out[field] = None
    return out
