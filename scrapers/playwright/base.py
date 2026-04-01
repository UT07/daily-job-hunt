"""Base scraper class for all Playwright-based scrapers.

Provides shared utilities: human-like delays, circuit breaker,
proxy configuration, scrape_runs reporting, and normalization.
"""
import hashlib
import html
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_supabase():
    """Create Supabase client from environment or SSM."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return create_client(url, key)
    # Fall back to SSM
    import boto3
    ssm = boto3.client("ssm")
    url = ssm.get_parameter(Name="/naukribaba/SUPABASE_URL", WithDecryption=True)["Parameter"]["Value"]
    key = ssm.get_parameter(Name="/naukribaba/SUPABASE_SERVICE_KEY", WithDecryption=True)["Parameter"]["Value"]
    return create_client(url, key)


def human_delay(min_s=2.0, max_s=5.0):
    """Gaussian-distributed delay for human-like browsing."""
    delay = random.gauss((min_s + max_s) / 2, (max_s - min_s) / 4)
    delay = max(min_s, min(max_s, delay))
    time.sleep(delay)


def normalize_text(text: str) -> str:
    """Clean HTML entities, tags, and whitespace from text."""
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def make_job_hash(company: str, title: str, description: str) -> str:
    """Generate a consistent hash for dedup."""
    key = f"{company.lower().strip()}|{title.lower().strip()}|{description[:500].lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


class BaseScraper(ABC):
    """Base class for all Playwright scrapers.

    Handles: Supabase connection, scrape_runs reporting, circuit breaking,
    proxy configuration, and job normalization.
    """

    SOURCE: str = ""  # Override in subclass
    MAX_JOBS: int = 50
    MIN_DELAY: float = 2.0
    MAX_DELAY: float = 5.0
    MAX_CONSECUTIVE_FAILURES: int = 3
    USE_PROXY: bool = True

    def __init__(self):
        self.db = get_supabase()
        self.pipeline_run_id = os.environ.get("PIPELINE_RUN_ID", "local")
        self.proxy_url = os.environ.get("PROXY_URL") if self.USE_PROXY else None
        self.query_hash = os.environ.get("QUERY_HASH", "default")
        self.new_job_hashes = []
        self.jobs_found = 0
        self.consecutive_failures = 0

    def _report_start(self):
        """Write scrape_runs row at start."""
        try:
            self.db.table("scrape_runs").insert({
                "pipeline_run_id": self.pipeline_run_id,
                "source": self.SOURCE,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to write scrape_runs start: {e}")

    def _report_complete(self, status="completed", error_message=None, blocked_reason=None):
        """Update scrape_runs row on completion."""
        try:
            update = {
                "status": status,
                "jobs_found": self.jobs_found,
                "jobs_new": len(self.new_job_hashes),
                "new_job_hashes": self.new_job_hashes,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            if error_message:
                update["error_message"] = error_message
            if blocked_reason:
                update["blocked_reason"] = blocked_reason
            self.db.table("scrape_runs").update(update) \
                .eq("pipeline_run_id", self.pipeline_run_id) \
                .eq("source", self.SOURCE) \
                .execute()
        except Exception as e:
            logger.warning(f"Failed to update scrape_runs: {e}")

    def _is_cached(self, job_hash: str) -> bool:
        """Check if job_hash already exists in jobs_raw (within 24h)."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        result = self.db.table("jobs_raw").select("job_hash", count="exact") \
            .eq("job_hash", job_hash) \
            .gte("scraped_at", cutoff) \
            .execute()
        return (result.count or 0) > 0

    def _save_job(self, job: dict):
        """Normalize and save a job to jobs_raw. Returns True if new."""
        title = normalize_text(job.get("title", ""))
        company = normalize_text(job.get("company", ""))
        description = normalize_text(job.get("description", ""))

        if not title or not company:
            return False

        job_hash = make_job_hash(company, title, description)

        if self._is_cached(job_hash):
            # Update last_seen for freshness tracking
            try:
                self.db.table("jobs_raw").update({
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }).eq("job_hash", job_hash).execute()
            except Exception:
                pass
            return False

        row = {
            "job_hash": job_hash,
            "title": title[:500],
            "company": company[:200],
            "description": description[:10000],
            "location": normalize_text(job.get("location", ""))[:200],
            "apply_url": (job.get("apply_url") or "")[:1000],
            "source": self.SOURCE,
            "experience_level": job.get("experience_level"),
            "job_type": job.get("job_type"),
            "salary": job.get("salary"),
            "query_hash": self.query_hash,
            "description_quality": job.get("description_quality", "full"),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.db.table("jobs_raw").upsert(row, on_conflict="job_hash").execute()
            self.new_job_hashes.append(job_hash)
            self.jobs_found += 1
            logger.info(f"[{self.SOURCE}] Saved: {title} at {company}")
            return True
        except Exception as e:
            logger.error(f"[{self.SOURCE}] DB save failed for {title}: {e}")
            return False

    def _circuit_break(self) -> bool:
        """Check if we should stop scraping due to consecutive failures."""
        if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning(f"[{self.SOURCE}] Circuit breaker: {self.consecutive_failures} consecutive failures")
            return True
        return False

    @abstractmethod
    def scrape(self, queries: list[str]) -> list[dict]:
        """Scrape jobs for the given queries. Returns list of raw job dicts."""
        ...

    def run(self):
        """Main entry point. Handles reporting, error handling, and circuit breaking."""
        queries_raw = os.environ.get("SCRAPE_QUERIES", "software engineer")
        queries = [q.strip() for q in queries_raw.split(",")]
        location = os.environ.get("SCRAPE_LOCATION", "Ireland")

        logger.info(f"[{self.SOURCE}] Starting scrape: queries={queries}, location={location}")
        self._report_start()

        try:
            jobs = self.scrape(queries)
            for job in jobs[:self.MAX_JOBS]:
                self._save_job(job)
                if self._circuit_break():
                    break

            self._report_complete(
                status="completed" if self.consecutive_failures < self.MAX_CONSECUTIVE_FAILURES else "blocked",
                blocked_reason="consecutive_failures" if self._circuit_break() else None,
            )
            logger.info(f"[{self.SOURCE}] Done: {self.jobs_found} found, {len(self.new_job_hashes)} new")

        except Exception as e:
            logger.error(f"[{self.SOURCE}] Scraper failed: {e}", exc_info=True)
            self._report_complete(status="failed", error_message=str(e))
            raise
