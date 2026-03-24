"""Supabase client wrapper for the Job Automation SaaS platform.

Thin wrapper around the Supabase Python client providing typed CRUD
operations for all multi-tenant tables: users, user_resumes,
user_search_configs, jobs, and runs.

Requires environment variables:
    SUPABASE_URL        — Supabase project URL (e.g. https://xxx.supabase.co)
    SUPABASE_SERVICE_KEY — Service role key (bypasses RLS for server-side ops)
"""

from __future__ import annotations
import logging
import os
from datetime import date, time, datetime
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Supabase client for the job automation multi-tenant database.

    Uses the service role key to bypass RLS — caller is responsible for
    passing the correct user_id to scope all queries to one tenant.
    """

    def __init__(self, url: str, service_key: str):
        self.client: Client = create_client(url, service_key)
        logger.info("[DB] Supabase client initialized")

    @classmethod
    def from_env(cls) -> SupabaseClient:
        """Create a client from SUPABASE_URL and SUPABASE_SERVICE_KEY env vars."""
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError(
                "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables"
            )
        return cls(url, key)

    # ── User CRUD ─────────────────────────────────────────────────

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a user by ID. Returns None if not found."""
        result = (
            self.client.table("users")
            .select("*")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        return result.data

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user. data must include 'id' and 'email' at minimum."""
        result = self.client.table("users").insert(data).execute()
        logger.info(f"[DB] Created user {data.get('email')}")
        return result.data[0]

    def update_user(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update user fields. Returns the updated row."""
        result = (
            self.client.table("users")
            .update(data)
            .eq("id", user_id)
            .execute()
        )
        logger.info(f"[DB] Updated user {user_id}")
        return result.data[0]

    # ── Resume CRUD ───────────────────────────────────────────────

    def get_resumes(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all resumes for a user."""
        result = (
            self.client.table("user_resumes")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at")
            .execute()
        )
        return result.data

    def upsert_resume(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a resume. data must include 'resume_key'.

        Uses the (user_id, resume_key) unique constraint for upsert.
        """
        data["user_id"] = user_id
        result = (
            self.client.table("user_resumes")
            .upsert(data, on_conflict="user_id,resume_key")
            .execute()
        )
        logger.info(f"[DB] Upserted resume '{data.get('resume_key')}' for user {user_id}")
        return result.data[0]

    def delete_resume(self, resume_id: str) -> None:
        """Delete a resume by its primary key ID."""
        self.client.table("user_resumes").delete().eq("id", resume_id).execute()
        logger.info(f"[DB] Deleted resume {resume_id}")

    # ── Search Config ─────────────────────────────────────────────

    def get_search_config(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the search config for a user. Returns None if not set."""
        result = (
            self.client.table("user_search_configs")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return result.data

    def upsert_search_config(
        self, user_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Insert or update search config. Uses the user_id unique constraint."""
        data["user_id"] = user_id
        result = (
            self.client.table("user_search_configs")
            .upsert(data, on_conflict="user_id")
            .execute()
        )
        logger.info(f"[DB] Upserted search config for user {user_id}")
        return result.data[0]

    # ── Jobs ──────────────────────────────────────────────────────

    def upsert_job(self, user_id: str, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a job. job_data must include 'job_id'.

        Uses the (job_id, user_id) composite primary key for upsert.
        On conflict, updates last_seen and any new fields.
        """
        job_data["user_id"] = user_id
        result = (
            self.client.table("jobs")
            .upsert(job_data, on_conflict="job_id,user_id")
            .execute()
        )
        return result.data[0]

    def get_jobs(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> List[Dict[str, Any]]:
        """Get paginated jobs for a user with optional filters.

        Supported filters:
            source       — exact match on job source
            min_score    — match_score >= value
            status       — exact match on application_status
            company      — exact match on company name
        """
        query = (
            self.client.table("jobs")
            .select("*")
            .eq("user_id", user_id)
        )

        if filters:
            if "source" in filters:
                query = query.eq("source", filters["source"])
            if "min_score" in filters:
                query = query.gte("match_score", filters["min_score"])
            if "status" in filters:
                query = query.eq("application_status", filters["status"])
            if "company" in filters:
                query = query.eq("company", filters["company"])

        offset = (page - 1) * per_page
        query = query.order("first_seen", desc=True).range(offset, offset + per_page - 1)

        result = query.execute()
        return result.data

    def update_job_status(
        self, user_id: str, job_id: str, status: str
    ) -> Dict[str, Any]:
        """Update a job's application status."""
        result = (
            self.client.table("jobs")
            .update({"application_status": status})
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        logger.info(f"[DB] Job {job_id} status -> {status}")
        return result.data[0]

    def get_job_stats(self, user_id: str) -> Dict[str, Any]:
        """Get aggregate job stats for a user.

        Returns dict with total_jobs, matched_jobs, avg_match_score,
        and jobs_by_status counts.
        """
        # Total jobs
        all_jobs = (
            self.client.table("jobs")
            .select("match_score, application_status")
            .eq("user_id", user_id)
            .execute()
        )
        rows = all_jobs.data

        total = len(rows)
        matched = sum(1 for r in rows if (r.get("match_score") or 0) > 0)
        scores = [r["match_score"] for r in rows if (r.get("match_score") or 0) > 0]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

        # Count by status
        status_counts: Dict[str, int] = {}
        for r in rows:
            s = r.get("application_status", "New")
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "total_jobs": total,
            "matched_jobs": matched,
            "avg_match_score": avg_score,
            "jobs_by_status": status_counts,
        }

    # ── Runs ──────────────────────────────────────────────────────

    def start_run(self, user_id: str, run_date: date) -> Dict[str, Any]:
        """Record a new pipeline run. Returns the created run row (includes run_id)."""
        now = datetime.utcnow()
        result = (
            self.client.table("runs")
            .insert({
                "user_id": user_id,
                "run_date": run_date.isoformat(),
                "run_time": now.strftime("%H:%M:%S"),
            })
            .execute()
        )
        run = result.data[0]
        logger.info(f"[DB] Started run {run['run_id']} for user {user_id}")
        return run

    def complete_run(self, run_id: str, stats: Dict[str, Any]) -> Dict[str, Any]:
        """Mark a run as complete with final stats.

        stats can include: raw_jobs, unique_jobs, matched_jobs, resumes_generated.
        """
        update_data = {**stats, "status": "completed", "completed_at": datetime.utcnow().isoformat()}
        result = (
            self.client.table("runs")
            .update(update_data)
            .eq("run_id", run_id)
            .execute()
        )
        logger.info(f"[DB] Completed run {run_id}")
        return result.data[0]

    def get_runs(
        self, user_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get recent pipeline runs for a user, newest first."""
        result = (
            self.client.table("runs")
            .select("*")
            .eq("user_id", user_id)
            .order("run_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
