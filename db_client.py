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
from datetime import date, time, datetime, timedelta
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
        # maybe_single().execute() returns None when no row matches
        if result is None:
            return None
        return result.data

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user. data must include 'id' and 'email' at minimum.

        Uses upsert on 'id' so re-provisioning the same auth user is idempotent.
        """
        result = (
            self.client.table("users")
            .upsert(data, on_conflict="id")
            .execute()
        )
        logger.info(f"[DB] Upserted user {data.get('email')}")
        return result.data[0]

    def update_user(self, user_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update user fields. Returns the updated row, or None if user not found."""
        result = (
            self.client.table("users")
            .update(data)
            .eq("id", user_id)
            .execute()
        )
        if not result.data:
            return None
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

    def delete_resume(self, resume_id: str, user_id: str) -> None:
        """Delete a resume by primary key, scoped to the owning user."""
        result = (
            self.client.table("user_resumes")
            .delete()
            .eq("id", resume_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            raise ValueError(f"Resume {resume_id} not found for user")
        logger.info(f"[DB] Deleted resume {resume_id} for user {user_id}")

    # ── Search Config ─────────────────────────────────────────────

    def get_search_config(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the search config for a user. Returns None if not set."""
        result = (
            self.client.table("user_search_configs")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return result.data[0] if result.data else None

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
    ):
        """Get paginated jobs for a user with optional filters.

        Returns a tuple of (rows, total_count).

        Supported filters:
            source       — exact match on job source
            min_score    — match_score >= value
            status       — exact match on application_status
            company      — substring match on company name
            tailored     — if "true", only return jobs with resume_s3_url
            tier         — exact match on score_tier (S, A, B, C, D) or comma-separated (S,A)
            hide_expired — if True, exclude expired jobs
        """
        query = (
            self.client.table("jobs")
            .select("*", count="exact")
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
                query = query.ilike("company", f"%{filters['company']}%")
            if filters.get("tailored") == "true":
                query = query.neq("resume_s3_url", None).neq("resume_s3_url", "")
            if "tier" in filters:
                tiers = [t.strip() for t in filters["tier"].split(",")]
                if len(tiers) == 1:
                    query = query.eq("score_tier", tiers[0])
                else:
                    query = query.in_("score_tier", tiers)
            if filters.get("hide_expired"):
                query = query.eq("is_expired", False)
            if "archetype" in filters:
                query = query.eq("archetype", filters["archetype"])
            if "seniority" in filters:
                query = query.eq("seniority", filters["seniority"])
            if "remote" in filters:
                query = query.eq("remote", filters["remote"])
            if "level_fit" in filters:
                query = query.eq("level_fit", filters["level_fit"])

        # Sorting — supports sort_by and sort_order from frontend
        sort_by = filters.get("sort_by", "first_seen") if filters else "first_seen"
        sort_order = filters.get("sort_order", "desc") if filters else "desc"
        valid_sort_fields = {"first_seen", "match_score", "title", "company", "application_status", "posted_date"}
        if sort_by not in valid_sort_fields:
            sort_by = "first_seen"

        offset = (page - 1) * per_page
        query = query.order(sort_by, desc=(sort_order == "desc")).range(offset, offset + per_page - 1)

        result = query.execute()
        total = result.count if result.count is not None else len(result.data)
        return result.data, total

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
        if not result.data:
            raise ValueError(f"Job {job_id} not found for user {user_id}")
        logger.info(f"[DB] Job {job_id} status -> {status}")
        return result.data[0]

    def delete_job(self, user_id: str, job_id: str) -> None:
        """Delete a job by ID, scoped to the owning user."""
        result = (
            self.client.table("jobs")
            .delete()
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            raise ValueError(f"Job {job_id} not found for user {user_id}")
        logger.info(f"[DB] Deleted job {job_id} for user {user_id}")

    def get_job_stats(self, user_id: str) -> Dict[str, Any]:
        """Get aggregate job stats for a user.

        Uses minimal SELECT (only 2 columns) for speed.
        Returns dict with total_jobs, matched_jobs, avg_match_score,
        and jobs_by_status counts.
        """
        all_jobs = (
            self.client.table("jobs")
            .select("match_score, application_status")
            .eq("user_id", user_id)
            .eq("is_expired", False)
            .execute()
        )
        rows = all_jobs.data or []

        total = len(rows)
        scores = [r["match_score"] for r in rows if (r.get("match_score") or 0) > 0]
        matched = len(scores)
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

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

    def fail_run(self, run_id: str, error: str = "Pipeline cancelled or timed out") -> Dict[str, Any]:
        """Mark a run as failed."""
        update_data = {"status": "failed", "completed_at": datetime.utcnow().isoformat()}
        result = (
            self.client.table("runs")
            .update(update_data)
            .eq("run_id", run_id)
            .execute()
        )
        logger.info(f"[DB] Failed run {run_id}: {error}")
        return result.data[0] if result.data else {}

    def cleanup_stale_runs(self, user_id: str, max_age_hours: int = 2) -> int:
        """Mark runs stuck in 'running' status for longer than max_age_hours as failed.

        Returns the number of runs cleaned up.
        """
        cutoff_date = (datetime.utcnow() - timedelta(hours=max_age_hours)).date().isoformat()
        # Find stale runs: status='running' and run_date before cutoff
        stale = (
            self.client.table("runs")
            .select("run_id")
            .eq("user_id", user_id)
            .eq("status", "running")
            .lt("run_date", cutoff_date)
            .execute()
        )
        count = 0
        for row in (stale.data or []):
            self.fail_run(row["run_id"], "Automatically marked as failed (stale)")
            count += 1
        if count:
            logger.info(f"[DB] Cleaned up {count} stale runs for user {user_id}")
        return count

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
