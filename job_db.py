"""SQLite job database for persistent storage across pipeline runs.

Replaces in-memory job lists with a queryable database. Tracks:
- All scraped jobs with dedup
- Match scores and AI results
- Pipeline run history
- Checkpoint state for crash recovery
"""

from __future__ import annotations
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from scrapers.base import Job

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    description TEXT,
    apply_url TEXT,
    source TEXT,
    posted_date TEXT,
    salary TEXT,
    job_type TEXT,
    remote BOOLEAN DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    match_score REAL DEFAULT 0,
    ats_score REAL DEFAULT 0,
    hiring_manager_score REAL DEFAULT 0,
    tech_recruiter_score REAL DEFAULT 0,
    matched_resume TEXT,
    match_reasoning TEXT,
    match_data TEXT,
    tailored_tex_path TEXT,
    tailored_pdf_path TEXT,
    cover_letter_tex_path TEXT,
    cover_letter_pdf_path TEXT,
    linkedin_contacts TEXT,
    applied TEXT DEFAULT 'No',
    application_status TEXT DEFAULT 'New'
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_time TEXT NOT NULL,
    raw_jobs INTEGER DEFAULT 0,
    unique_jobs INTEGER DEFAULT 0,
    new_jobs INTEGER DEFAULT 0,
    matched_jobs INTEGER DEFAULT 0,
    resumes_generated INTEGER DEFAULT 0,
    cover_letters_generated INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    checkpoint_step INTEGER DEFAULT 0,
    checkpoint_data TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
"""


class JobDatabase:
    """SQLite-backed job database with checkpoint support."""

    def __init__(self, db_path: str = "output/jobs.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── Job Operations ──────────────────────────────────────────────

    def upsert_job(self, job: Job, run_date: str):
        """Insert or update a job. Returns True if this is a new job."""
        existing = self._conn.execute(
            "SELECT job_id, first_seen FROM jobs WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()

        if existing:
            self._conn.execute(
                "UPDATE jobs SET last_seen = ?, description = CASE WHEN length(?) > length(COALESCE(description, '')) THEN ? ELSE description END WHERE job_id = ?",
                (run_date, job.description or "", job.description or "", job.job_id),
            )
            self._conn.commit()
            return False
        else:
            self._conn.execute(
                """INSERT INTO jobs (job_id, title, company, location, description,
                   apply_url, source, posted_date, salary, job_type, remote,
                   first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job.job_id, job.title, job.company, job.location,
                 job.description, job.apply_url, job.source, job.posted_date,
                 job.salary, job.job_type, job.remote, run_date, run_date),
            )
            self._conn.commit()
            return True

    def upsert_jobs(self, jobs: List[Job], run_date: str) -> tuple[int, int]:
        """Bulk upsert. Returns (new_count, existing_count)."""
        new = 0
        existing = 0
        for job in jobs:
            if self.upsert_job(job, run_date):
                new += 1
            else:
                existing += 1
        return new, existing

    def get_new_jobs(self, jobs: List[Job], run_date: str) -> List[Job]:
        """Filter to only jobs not already in the database. Also inserts new ones."""
        new_jobs = []
        for job in jobs:
            if self.upsert_job(job, run_date):
                new_jobs.append(job)
        return new_jobs

    def update_match_scores(self, job: Job):
        """Update a job's match scores after AI matching."""
        self._conn.execute(
            """UPDATE jobs SET match_score = ?, ats_score = ?,
               hiring_manager_score = ?, tech_recruiter_score = ?,
               matched_resume = ?, match_reasoning = ?,
               match_data = ?
               WHERE job_id = ?""",
            (job.match_score, job.ats_score, job.hiring_manager_score,
             job.tech_recruiter_score, job.matched_resume,
             job.match_reasoning,
             json.dumps(job._match_data) if hasattr(job, '_match_data') and job._match_data else None,
             job.job_id),
        )
        self._conn.commit()

    def update_artifacts(self, job: Job):
        """Update a job's generated artifact paths."""
        self._conn.execute(
            """UPDATE jobs SET tailored_tex_path = ?, tailored_pdf_path = ?,
               cover_letter_tex_path = ?, cover_letter_pdf_path = ?,
               linkedin_contacts = ?
               WHERE job_id = ?""",
            (job.tailored_tex_path, job.tailored_pdf_path,
             job.cover_letter_tex_path, job.cover_letter_pdf_path,
             job.linkedin_contacts, job.job_id),
        )
        self._conn.commit()

    def is_seen(self, job_id: str) -> bool:
        """Check if a job has been seen before."""
        row = self._conn.execute(
            "SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row is not None

    def get_stats(self, run_date: str = None) -> dict:
        """Get aggregate stats, optionally filtered by date."""
        where = "WHERE first_seen = ?" if run_date else ""
        params = (run_date,) if run_date else ()

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM jobs {where}", params
        ).fetchone()[0]

        matched = self._conn.execute(
            f"SELECT COUNT(*) FROM jobs {where} {'AND' if where else 'WHERE'} match_score > 0",
            params,
        ).fetchone()[0]

        avg_score = self._conn.execute(
            f"SELECT AVG(match_score) FROM jobs {where} {'AND' if where else 'WHERE'} match_score > 0",
            params,
        ).fetchone()[0]

        return {
            "total_jobs": total,
            "matched_jobs": matched,
            "avg_match_score": round(avg_score, 1) if avg_score else 0,
        }

    # ── Run / Checkpoint Operations ─────────────────────────────────

    def start_run(self, run_date: str, run_time: str) -> int:
        """Record a new pipeline run. Returns run_id."""
        cursor = self._conn.execute(
            "INSERT INTO runs (run_date, run_time) VALUES (?, ?)",
            (run_date, run_time),
        )
        self._conn.commit()
        return cursor.lastrowid

    def update_run(self, run_id: int, **kwargs):
        """Update run metadata (raw_jobs, matched_jobs, status, etc.)."""
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [run_id]
        self._conn.execute(
            f"UPDATE runs SET {sets} WHERE run_id = ?", values
        )
        self._conn.commit()

    def save_checkpoint(self, run_id: int, step: int, data: dict = None):
        """Save pipeline checkpoint for crash recovery."""
        self._conn.execute(
            "UPDATE runs SET checkpoint_step = ?, checkpoint_data = ? WHERE run_id = ?",
            (step, json.dumps(data) if data else None, run_id),
        )
        self._conn.commit()
        logger.debug(f"Checkpoint saved: step {step}")

    def get_latest_checkpoint(self, run_date: str) -> Optional[dict]:
        """Get the latest incomplete run's checkpoint for today."""
        row = self._conn.execute(
            """SELECT run_id, checkpoint_step, checkpoint_data
               FROM runs WHERE run_date = ? AND status = 'running'
               ORDER BY run_id DESC LIMIT 1""",
            (run_date,),
        ).fetchone()

        if row:
            return {
                "run_id": row["run_id"],
                "step": row["checkpoint_step"],
                "data": json.loads(row["checkpoint_data"]) if row["checkpoint_data"] else {},
            }
        return None

    def complete_run(self, run_id: int, **stats):
        """Mark a run as complete with final stats."""
        stats["status"] = "completed"
        stats["completed_at"] = datetime.now().isoformat()
        self.update_run(run_id, **stats)

    def close(self):
        self._conn.close()
