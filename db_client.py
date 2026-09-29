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
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Matches both the raw Postgres wording (`column "x" of relation "y" does
# not exist`, e.g. what score_batch.py's own retry logic looks for) and
# PostgREST's schema-cache wording (`Could not find the 'x' column of 'y'
# in the schema cache`, the same shape already seen in this codebase for
# the match_jobs_semantic RPC in mcp_server.py) -- the two error shapes a
# write against a not-yet-migrated optional column can come back as.
_MISSING_COLUMN_PATTERNS = (
    re.compile(r'column "([^"]+)" of relation "[^"]+" does not exist'),
    re.compile(r"Could not find the '([^']+)' column of '[^']+' in the schema cache"),
)


def _missing_optional_column(exc: Exception) -> Optional[str]:
    """Return the column name if `exc` looks like an "unknown column" error
    from Postgres/PostgREST, else None so callers only swallow this one,
    narrow condition and re-raise everything else."""
    text = str(exc)
    for pattern in _MISSING_COLUMN_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


# Columns the dashboard list actually needs — everything on `jobs` EXCEPT
# `embedding`.
#
# `embedding` is the 768-dimension pgvector column used for semantic dedup and
# bullet retrieval. It is server-side data; nothing in web/src references it.
# select("*") was sending it across Supabase -> Lambda -> API Gateway -> browser
# on every page load, and the browser parsed it as JSON. Measured against
# production on 2026-09-29, 100 rows:
#
#     select("*")          0.423s   1987 KB   (embedding = 931 KB, 46.8%)
#     this list            0.162s    183 KB
#
# Spelled out rather than "all except" because PostgREST has no exclusion
# syntax. The cost is that a genuinely new column must be added here to reach
# the dashboard; test_dashboard_payload_weight pins the fields the UI reads so
# a removal breaks a test rather than a page.
JOB_LIST_COLUMNS = (
    "application_status,apply_board_token,apply_platform,apply_posting_id,"
    "apply_url,archetype,ats_score,base_ats_score,base_hm_score,"
    "base_tr_score,canonical_hash,company,company_research,"
    "cover_letter_model,cover_letter_pdf_path,cover_letter_s3_key,"
    "cover_letter_s3_url,"
    "description,easy_apply_eligible,failure_reason,final_score,"
    "first_seen,gaps,hiring_manager_score,interview_prep,is_expired,"
    "job_hash,job_id,key_matches,last_seen,level_fit,linkedin_contacts,"
    "location,match_reasoning,match_score,matched_resume,posted_date,"
    "remote,requirement_map,resume_doc_url,resume_s3_key,resume_s3_url,"
    "resume_version,score_status,score_tier,score_version,scored_at,"
    "seniority,source,tailored_ats_score,tailored_hm_score,"
    "tailored_pdf_path,tailored_tr_score,tailoring_model,"
    "tech_recruiter_score,title,trace_id,user_id,writing_quality_score"
)


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
        """Get the search config for a user. Returns None if not set.

        Uses `select("*")` rather than an explicit column list, so this
        already degrades gracefully when an optional column (e.g.
        enabled_sources, before its migration is applied) doesn't exist yet
        -- it just isn't in the returned dict, no error.
        """
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
        """Insert or update search config. Uses the user_id unique constraint.

        Degrades gracefully when `data` includes a column that doesn't exist
        in the live schema yet -- e.g. enabled_sources before
        supabase/migrations/20260506_user_search_configs_enabled_sources.sql
        is applied via the dashboard. Without this, PUT /api/search-config
        500s on every "Save Sources" click because the upsert raises and
        nothing catches it. Retries without the offending column instead,
        matching the retry-without-optional-columns pattern already used in
        score_batch.py for the `jobs` table.
        """
        payload = dict(data)
        payload["user_id"] = user_id
        while True:
            try:
                result = (
                    self.client.table("user_search_configs")
                    .upsert(payload, on_conflict="user_id")
                    .execute()
                )
                break
            except Exception as e:
                col = _missing_optional_column(e)
                if col is None or col not in payload:
                    raise
                logger.warning(
                    f"[DB] user_search_configs.{col} not in schema yet "
                    f"(migration pending) -- dropping from upsert for user "
                    f"{user_id}: {e}"
                )
                payload.pop(col)
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
            title        — substring match on job title
            tailored     — if "true", only return jobs with resume_s3_url
            tier         — exact match on score_tier (S, A, B, C, D) or comma-separated (S,A)
            hide_expired — if True, exclude expired jobs (engaged rows exempt)
            lifecycle    — "not_archived" (default, applied even when no
                           filters are passed), "active", "stale",
                           "archived", or "all". Age-based, measured from
                           first_seen; see shared/job_lifecycle.py.
        """
        query = (
            self.client.table("jobs")
            .select(JOB_LIST_COLUMNS, count="exact")
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
            if "title" in filters:
                query = query.ilike("title", f"%{filters['title']}%")
            if filters.get("tailored") == "true":
                query = query.neq("resume_s3_url", None).neq("resume_s3_url", "")
            if "tier" in filters:
                tiers = [t.strip() for t in filters["tier"].split(",")]
                if len(tiers) == 1:
                    query = query.eq("score_tier", tiers[0])
                else:
                    query = query.in_("score_tier", tiers)
            if filters.get("hide_expired"):
                # Engaged rows are exempt here too, not just from the age
                # thresholds. Verified against prod 2026-09-28: all 38
                # Applied/Withdrawn/Rejected rows carry is_expired=True (the
                # posting 404'd long after the user applied), so a plain
                # `.eq("is_expired", False)` erased every application the
                # user has ever sent from the default dashboard -- the
                # lifecycle exemption alone was letting them through the age
                # gate only for this filter to drop them one line later.
                from shared.job_lifecycle import ENGAGED_STATUSES as _ENGAGED
                query = query.or_(
                    f"is_expired.eq.false,"
                    f"application_status.in.({','.join(sorted(_ENGAGED))})"
                )
            if "archetype" in filters:
                query = query.eq("archetype", filters["archetype"])
            if "seniority" in filters:
                query = query.eq("seniority", filters["seniority"])
            if "remote" in filters:
                query = query.eq("remote", filters["remote"])
            if "level_fit" in filters:
                query = query.eq("level_fit", filters["level_fit"])
            if "skill" in filters:
                import json as _json
                query = query.filter("key_matches", "cs", _json.dumps([filters["skill"]]))

        # Age-based lifecycle, independent of is_expired (which only means the
        # apply_url 404'd). Default is to hide archived rows: a posting first
        # seen 4 months ago is not something the user can act on, and leaving
        # them in is what made the dashboard unreadable. Engaged rows
        # (Applied / Rejected / ...) are exempt at every threshold.
        #
        # Deliberately OUTSIDE the `if filters:` block above. It used to live
        # inside it, which meant the documented "not_archived" default silently
        # did not apply to a caller that passed no filters at all -- an
        # unfiltered GET /api/dashboard/jobs returned all 1,251 rows including
        # the 1,164 archived ones. Verified against prod 2026-09-28.
        lifecycle = (filters or {}).get("lifecycle", "not_archived")
        if lifecycle != "all":
            from shared.job_lifecycle import (
                ARCHIVE_AFTER_DAYS, ENGAGED_STATUSES, STALE_AFTER_DAYS, cutoff_iso,
            )
            engaged = ",".join(sorted(ENGAGED_STATUSES))
            if lifecycle == "not_archived":
                query = query.or_(
                    f"first_seen.gte.{cutoff_iso(ARCHIVE_AFTER_DAYS)},"
                    f"first_seen.is.null,"
                    f"application_status.in.({engaged})"
                )
            elif lifecycle == "active":
                query = query.or_(
                    f"first_seen.gte.{cutoff_iso(STALE_AFTER_DAYS)},"
                    f"first_seen.is.null,"
                    f"application_status.in.({engaged})"
                )
            elif lifecycle == "stale":
                query = (query
                         .lt("first_seen", cutoff_iso(STALE_AFTER_DAYS))
                         .gte("first_seen", cutoff_iso(ARCHIVE_AFTER_DAYS))
                         .not_.in_("application_status", list(ENGAGED_STATUSES)))
            elif lifecycle == "archived":
                query = (query
                         .lt("first_seen", cutoff_iso(ARCHIVE_AFTER_DAYS))
                         .not_.in_("application_status", list(ENGAGED_STATUSES)))

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

        Funnel metrics (`total_applied`, `total_interviewing`, `total_offers`,
        `total_rejected`) are computed from the application_timeline table so
        they reflect "anything that ever reached this stage" rather than
        the current bucket. Without that, marking Applied → New (or any
        non-funnel status) silently resets the Applied count to 0 — the
        F5 bug from the comprehensive prod-health initiative.
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

        # Funnel metrics — count distinct jobs that ever reached each stage,
        # via the application_timeline event log. Fall back to current-status
        # counts only when the timeline read fails (e.g. table missing in
        # local dev) so the dashboard still renders something useful.
        _APPLIED_STAGES = {"Applied", "Phone Screen", "Interview", "Offer",
                           "Rejected", "Withdrawn", "Accepted"}
        _INTERVIEWING_STAGES = {"Phone Screen", "Interview"}
        _OFFER_STAGES = {"Offer", "Accepted"}
        _REJECTED_STAGES = {"Rejected"}

        try:
            timeline = (
                self.client.table("application_timeline")
                .select("job_id, status")
                .eq("user_id", user_id)
                .execute()
            )
            events = timeline.data or []
        except Exception as e:  # pragma: no cover - belt-and-suspenders fallback
            logger.warning("application_timeline read failed: %s; using current-status fallback", e)
            events = None

        if events is not None:
            # Build per-job set of statuses ever reached, then derive funnel.
            jobs_to_stages: Dict[str, set] = {}
            for ev in events:
                jid = ev.get("job_id")
                stage = ev.get("status")
                if not jid or not stage:
                    continue
                jobs_to_stages.setdefault(jid, set()).add(stage)

            # Also fold in the current status — older jobs may have a current
            # status without any matching timeline event (e.g. PATCH-only
            # updates from StatusDropdown that bypass the timeline insert).
            jobs_with_status = (
                self.client.table("jobs")
                .select("job_id, application_status")
                .eq("user_id", user_id)
                .execute()
            )
            for r in jobs_with_status.data or []:
                jid = r.get("job_id")
                cur = r.get("application_status")
                if jid and cur:
                    jobs_to_stages.setdefault(jid, set()).add(cur)

            def _count(stages: set) -> int:
                return sum(1 for st in jobs_to_stages.values() if st & stages)

            total_applied = _count(_APPLIED_STAGES)
            total_interviewing = _count(_INTERVIEWING_STAGES)
            total_offers = _count(_OFFER_STAGES)
            total_rejected = _count(_REJECTED_STAGES)
        else:
            total_applied = sum(n for s, n in status_counts.items() if s in _APPLIED_STAGES)
            total_interviewing = sum(n for s, n in status_counts.items() if s in _INTERVIEWING_STAGES)
            total_offers = sum(n for s, n in status_counts.items() if s in _OFFER_STAGES)
            total_rejected = status_counts.get("Rejected", 0)

        return {
            "total_jobs": total,
            "matched_jobs": matched,
            "avg_match_score": avg_score,
            "jobs_by_status": status_counts,
            "total_applied": total_applied,
            "total_rejected": total_rejected,
            "total_interviewing": total_interviewing,
            "total_offers": total_offers,
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
