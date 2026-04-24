# shared/load_job.py
"""Single-row job lookup with user scoping."""

from __future__ import annotations
from typing import Optional


def load_job(job_id: str, user_id: str, *, db) -> Optional[dict]:
    """Load a jobs row by (job_id, user_id). None if not found."""
    resp = (
        db.client.table("jobs")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return resp.data
