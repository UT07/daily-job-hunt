"""FastAPI backend for NaukriBaba.

Endpoints:
- POST /api/pipeline/run            — start daily pipeline (Step Functions)
- POST /api/pipeline/run-single     — start single-job pipeline (Add Job)
- GET  /api/pipeline/status         — latest pipeline metrics
- GET  /api/pipeline/status/{name}  — poll specific execution
- POST /api/compile-latex           — compile LaTeX to PDF
- POST /api/score                   — score a JD against base resumes
- POST /api/tailor                  — tailor resume + compile PDF
- POST /api/cover-letter            — generate cover letter PDF
- POST /api/contacts                — find LinkedIn contacts
- GET  /api/profile                 — user profile
- PUT  /api/profile                 — update profile
- GET  /api/dashboard/jobs          — paginated job list
- PATCH /api/dashboard/jobs/{id}    — update job status
- DELETE /api/dashboard/jobs/{id}   — delete job
- GET  /api/dashboard/jobs/{id}/timeline — list application timeline events
- GET  /api/dashboard/jobs/{id}/versions — list resume versions (newest first)
- POST /api/dashboard/jobs/{id}/versions/{ver}/restore — restore a version as current
- GET  /api/dashboard/jobs/{id}/sections — parse .tex into editable sections + JD analysis
- POST /api/dashboard/jobs/{id}/sections — rebuild .tex from edited sections, compile PDF
- POST /api/dashboard/jobs/{id}/timeline — add a timeline event (also updates job status)
- POST /api/dashboard/jobs/{id}/generate-email — AI-generate a cold outreach / follow-up / thank-you email
- GET  /api/dashboard/stats         — aggregate metrics
- GET  /api/dashboard/runs          — run history
- POST /api/resumes/upload          — upload PDF resume
- GET  /api/resumes                 — list resumes
- DELETE /api/resumes/{id}          — delete resume
- POST /api/feedback/flag-score     — flag a score as inaccurate
- POST /api/gdpr/consent            — record GDPR consent
- GET  /api/gdpr/export             — export user data (Article 15)
- DELETE /api/gdpr/delete           — request deletion
- GET  /api/health                  — health check (public)

All endpoints except /api/health and /api/templates require a valid Supabase JWT.
"""

import io
import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Optional

import boto3

# Load .env file if present (for local development)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _val = _val.strip().strip("'\"")
                if _key.strip() and _val:
                    os.environ.setdefault(_key.strip(), _val)

import yaml
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import AuthUser, get_current_user
from audit_middleware import AuditMiddleware, set_db as set_audit_db
from db_client import SupabaseClient

from ai_client import AIClient
from contact_finder import find_contacts
from cover_letter import generate_cover_letter
from latex_compiler import compile_tex_to_pdf
from s3_uploader import upload_file as s3_upload_file
from matcher import match_jobs
from resume_scorer import score_and_improve
from tailorer import tailor_resume
from utils.canonical_hash import canonical_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def _initialize_state() -> None:
    """Initialize global config, AI client, resumes, and DB.

    Called from the FastAPI lifespan (normal HTTP server startup) and also
    imperatively from the SQS Lambda handler (which doesn't go through
    lifespan on cold start). Safe to call multiple times — reassigns globals.
    """
    global _ai_client, _config, _resumes, _db, _posthog
    _config = _load_config()
    _resumes = _load_resumes(_config)
    try:
        _ai_client = AIClient.from_config(_config)
    except Exception as e:
        import traceback
        logger.error("AI client init failed: %s\n%s", e, traceback.format_exc())
        _ai_client = None
    try:
        _db = SupabaseClient.from_env()
    except RuntimeError:
        logger.warning("Supabase not configured — profile endpoints will fail")
        _db = None
    # Wire up audit middleware with the DB client (no-ops gracefully if _db is None)
    set_audit_db(_db)
    # Initialize PostHog (analytics capture + feature-flag evaluation share one client)
    _ph_key = os.environ.get("POSTHOG_API_KEY", "")
    _ph_host = os.environ.get("POSTHOG_HOST", "https://eu.i.posthog.com")
    if _ph_key:
        import atexit
        _posthog = _Posthog(
            project_api_key=_ph_key,
            host=_ph_host,
            enable_exception_autocapture=True,
        )
        atexit.register(_posthog.shutdown)
        logger.info("PostHog analytics enabled")
    else:
        logger.warning("POSTHOG_API_KEY not set — analytics disabled")
    logger.info("API started — %d resumes loaded, AI client ready", len(_resumes))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _initialize_state()
    yield


app = FastAPI(title="Job Hunt API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://naukribaba.netlify.app", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Audit trail middleware — DB reference is set in startup() via set_audit_db()
app.add_middleware(AuditMiddleware)

# Global state (initialized on startup)
_ai_client: Optional[AIClient] = None
_config: dict = {}
_resumes: dict[str, str] = {}  # {key: tex_content}
_db: Optional[SupabaseClient] = None

# PostHog client (instance-based API)
from posthog import Posthog as _Posthog
_posthog: Optional[_Posthog] = None

# Task store — Supabase pipeline_tasks table (persistent across cold starts)


def _resolve_env(value: str) -> str:
    """Resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    # Resolve env vars in api_keys
    if "api_keys" in config:
        config["api_keys"] = {
            k: _resolve_env(v) for k, v in config["api_keys"].items()
        }
    return config


def _load_resumes(config: dict) -> dict[str, str]:
    """Load base resume .tex files into memory."""
    resumes = {}
    base = Path(__file__).parent
    for key, info in config.get("resumes", {}).items():
        tex_path = base / info["tex_path"]
        if tex_path.exists():
            resumes[key] = tex_path.read_text()
        else:
            logger.warning("Resume file not found: %s", tex_path)
    return resumes


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ScoreRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"
    resume_type: str = Field("sre_devops", description="Resume key from config")


class ScoreResponse(BaseModel):
    ats_score: float
    hiring_manager_score: float
    tech_recruiter_score: float
    avg_score: float
    reasoning: str
    matched_resume: str


class TailorRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"
    resume_type: str = "sre_devops"


class TailorResponse(BaseModel):
    ats_score: int
    hiring_manager_score: int
    tech_recruiter_score: int
    avg_score: int
    pdf_url: str


class CoverLetterRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"
    resume_type: str = "sre_devops"


class CoverLetterResponse(BaseModel):
    pdf_url: str


class ContactsRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"


class Contact(BaseModel):
    name: str = ""
    role: str
    role_type: str = ""
    why: str
    message: str
    profile_url: str = ""
    search_url: str
    google_url: str = ""


class ContactsResponse(BaseModel):
    contacts: list[Contact]


class FlagScoreRequest(BaseModel):
    job_id: str = Field(..., min_length=1, description="ID of the job whose score is being flagged")
    feedback_type: str = Field("score_inaccurate", description="Type of feedback")
    expected_score: Optional[int] = Field(None, ge=0, le=100, description="What the user thinks the score should be")
    comment: Optional[str] = Field(None, max_length=1000, description="Free-text explanation")


class ProfileResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None
    visa_status: Optional[str] = None
    work_authorizations: Optional[dict] = None
    candidate_context: Optional[str] = None
    plan: str = "free"
    created_at: Optional[str] = None
    gdpr_consent_at: Optional[str] = None
    salary_expectation_notes: str = ""
    notice_period_text: str = ""
    onboarding_completed_at: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None  # alias
    phone: Optional[str] = None
    location: Optional[str] = None
    visa_status: Optional[str] = None
    github: Optional[str] = None
    github_url: Optional[str] = None  # alias
    linkedin: Optional[str] = None
    linkedin_url: Optional[str] = None  # alias
    website: Optional[str] = None
    work_authorizations: Optional[dict] = None
    candidate_context: Optional[str] = None
    target_roles: Optional[list[str]] = None
    target_locations: Optional[list[str]] = None
    salary_expectation_notes: Optional[str] = None
    notice_period_text: Optional[str] = None
    complete_onboarding: Optional[bool] = None


# ---------------------------------------------------------------------------
# Helper: create a minimal Job object for the pipeline modules
# ---------------------------------------------------------------------------

class _Job:
    """Lightweight job object compatible with pipeline modules."""

    def __init__(self, title: str, company: str, description: str):
        self.job_id = f"web-{hash(title + company + description[:50]) & 0xFFFFFFFF:08x}"
        self.title = title
        self.company = company
        self.description = description
        self.location = ""
        self.apply_url = ""
        self.source = "web"
        self.posted_date = ""
        self.job_type = "Full-time"
        self.salary = None
        self.remote = False
        self.experience_level = None
        self.scraped_at = ""
        # Fields set by pipeline modules
        self.ats_score = 0
        self.hiring_manager_score = 0
        self.tech_recruiter_score = 0
        self.match_score = 0
        self.match_reasoning = ""
        self.matched_resume = ""
        self.tailored_tex_path = ""
        self.tailored_pdf_path = ""
        self.cover_letter_tex_path = ""
        self.cover_letter_pdf_path = ""
        self.linkedin_contacts = "[]"
        self._match_data = {}




# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/quality-stats")
def get_quality_stats(user: AuthUser = Depends(get_current_user)):
    """Get AI model quality statistics."""
    from quality_logger import get_model_stats, read_quality_log
    stats = get_model_stats()
    recent = read_quality_log(limit=50)
    return {"model_stats": stats, "recent_logs": recent}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "resumes_loaded": list(_resumes.keys()),
        "ai_providers": len(_ai_client.providers) if _ai_client else 0,
    }


@app.get("/api/templates")
def get_templates():
    from template_engine import list_templates
    return {"templates": list_templates()}


@app.post("/api/score", response_model=ScoreResponse)
def score_job(req: ScoreRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}. Available: {list(_resumes.keys())}")

    job = _Job(req.job_title, req.company, req.job_description)
    resumes = {req.resume_type: _resumes[req.resume_type]}

    try:
        matched = match_jobs([job], resumes, _ai_client, min_score=0, batch_size=1)
    except Exception as e:
        logger.error("Scoring failed: %s", e)
        raise HTTPException(500, f"AI scoring failed: {e}")

    if not matched:
        return ScoreResponse(
            ats_score=0, hiring_manager_score=0, tech_recruiter_score=0,
            avg_score=0, reasoning="Job did not match any criteria.", matched_resume=req.resume_type,
        )

    j = matched[0]
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="job_scored",
            properties={
                "resume_type": req.resume_type,
                "avg_score": j.match_score,
                "jd_length": len(req.job_description),
            },
        )
    return ScoreResponse(
        ats_score=j.ats_score,
        hiring_manager_score=j.hiring_manager_score,
        tech_recruiter_score=j.tech_recruiter_score,
        avg_score=j.match_score,
        reasoning=j.match_reasoning,
        matched_resume=j.matched_resume or req.resume_type,
    )


# ---------------------------------------------------------------------------
# Async task helpers — lets long-running endpoints return 202 + task_id
# so API Gateway's 29s timeout doesn't kill them.
# ---------------------------------------------------------------------------

TASK_QUEUE_URL = os.environ.get("TASK_QUEUE_URL", "")


def _save_task(task_id: str, user_id: str, data: dict):
    """Persist task state to Supabase pipeline_tasks table."""
    if not _db:
        logger.warning("_save_task: database not configured, skipping persist for %s", task_id)
        return
    row = {
        "task_id": task_id,
        "user_id": user_id,
        "status": data.get("status", "running"),
        "result": data.get("result"),
        "error": data.get("error"),
        "payload": data.get("payload"),
    }
    _db.client.table("pipeline_tasks").upsert(row, on_conflict="task_id").execute()


def _load_task(task_id: str) -> dict | None:
    """Load task state from Supabase."""
    if not _db:
        return None
    result = _db.client.table("pipeline_tasks").select("*").eq("task_id", task_id).maybe_single().execute()
    if not result or not result.data:
        return None
    row = result.data
    task = {"status": row["status"]}
    if row.get("result"):
        task["result"] = row["result"]
    if row.get("error"):
        task["error"] = row["error"]
    return task


_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    return _s3_client


def _refresh_s3_urls(jobs: list) -> list:
    """Regenerate presigned URLs from stored S3 keys so they never expire."""
    bucket = os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))
    s3 = _get_s3()
    for job in jobs:
        for key_field, url_field in [
            ("resume_s3_key", "resume_s3_url"),
            ("cover_letter_s3_key", "cover_letter_s3_url"),
        ]:
            s3_key = job.get(key_field)
            if s3_key:
                try:
                    job[url_field] = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": bucket, "Key": s3_key},
                        ExpiresIn=7 * 24 * 3600,  # 7 days
                    )
                except Exception:
                    pass  # keep existing URL
    return jobs


def _enqueue_task(task_id: str, user_id: str, task_type: str, payload: dict):
    """Save task to Supabase then send an SQS message (or fall back to threading for local dev)."""
    _save_task(task_id, user_id, {"status": "running", "payload": payload})

    sqs_message = json.dumps({"task_id": task_id, "task_type": task_type})

    if TASK_QUEUE_URL:
        # Production path: send to SQS
        sqs = boto3.client("sqs")
        sqs.send_message(QueueUrl=TASK_QUEUE_URL, MessageBody=sqs_message)
        logger.info("Enqueued task %s (%s) to SQS", task_id, task_type)
    else:
        # Local dev fallback: run in a background thread (same as old behaviour)
        logger.info("TASK_QUEUE_URL not set — running task %s (%s) in background thread", task_id, task_type)

        def _local_worker():
            try:
                result = _dispatch_task(task_type, payload, user_id=user_id)
                _save_task(task_id, user_id, {"status": "done", "result": result})
            except Exception as e:
                logger.error("Background task %s failed: %s", task_id, e)
                _save_task(task_id, user_id, {"status": "error", "error": str(e)})

        threading.Thread(target=_local_worker, daemon=True).start()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: AuthUser = Depends(get_current_user)):
    """Poll for the result of an async task."""
    task = _load_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


# ---------------------------------------------------------------------------
# SQS task dispatch + processing
# ---------------------------------------------------------------------------

def merge_manual_job(existing: dict, manual: dict) -> dict:
    """Merge manual JD with existing scraped job. Manual wins for all fields."""
    merged = {**existing}
    for key in ("description", "title", "company", "location", "apply_url", "source"):
        if manual.get(key):
            merged[key] = manual[key]
    return merged


def _find_or_create_job(user_id: str, payload: dict) -> str:
    """Check if job exists in Supabase by canonical hash. Merge if found, create if not. Return job_id."""
    if not _db:
        return ""
    company = payload.get("company", "Unknown")
    title = payload.get("job_title", "Software Engineer")
    description = payload.get("job_description", "")

    # Compute canonical hash for dedup
    chash = canonical_hash(company, title, description)

    # Look up by canonical_hash for this user
    try:
        result = (
            _db.client.table("jobs")
            .select("*")
            .eq("user_id", user_id)
            .eq("canonical_hash", chash)
            .maybe_single()
            .execute()
        )
        if result and result.data:
            # Found existing job — merge manual data (manual wins)
            existing = result.data
            manual_data = {
                "description": description,
                "title": title,
                "company": company,
                "source": "manual",
            }
            merged = merge_manual_job(existing, manual_data)
            # Update the existing row with merged data
            _db.client.table("jobs").update({
                "description": merged["description"],
                "title": merged["title"],
                "company": merged["company"],
                "source": merged["source"],
            }).eq("job_id", existing["job_id"]).execute()
            logger.info("Merged manual JD into existing job %s (hash=%s)", existing["job_id"], chash)
            return existing["job_id"]
    except Exception as e:
        logger.warning("Job lookup by canonical_hash failed: %s", e)

    # No match — create new job entry
    import datetime
    row = {
        "job_id": chash,
        "user_id": user_id,
        "canonical_hash": chash,
        "title": title,
        "company": company,
        "description": description,
        "source": "manual",
        "application_status": "New",
        "first_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _db.client.table("jobs").insert(row).execute()
    except Exception as e:
        logger.warning("Job creation failed: %s", e)
    return chash


def _update_job_artifacts(job_id: str, updates: dict):
    """Update a job row with tailor/cover-letter/contacts results."""
    if not _db or not job_id:
        return
    try:
        _db.client.table("jobs").update(updates).eq("job_id", job_id).execute()
    except Exception as e:
        logger.warning(f"Job artifact update failed: {e}")


def _dispatch_task(task_type: str, payload: dict, user_id: str = "") -> dict:
    """Route a task to the appropriate worker function based on task_type."""
    job = _Job(
        title=payload.get("job_title", "Software Engineer"),
        company=payload.get("company", "Unknown"),
        description=payload.get("job_description", ""),
    )

    # Find or create job in dashboard — use explicit job_id if provided (from dashboard endpoints)
    job_id = payload.get("job_id") or (_find_or_create_job(user_id, payload) if user_id else "")

    if task_type == "tailor":
        resume_type = payload.get("resume_type", "sre_devops")
        base_tex = _resumes.get(resume_type, "")
        result = _do_tailor(job, base_tex, resume_type, payload.get("company", "Unknown"), payload.get("job_title", "Software Engineer"))
        # Save artifacts to dashboard job — use actual model that won the council vote
        tailoring_model = f"{getattr(job, 'tailoring_provider', 'council')}:{getattr(job, 'tailoring_model', 'consensus')}"
        _update_job_artifacts(job_id, {
            "resume_s3_url": result.get("pdf_url", ""),
            "ats_score": result.get("ats_score", 0),
            "hiring_manager_score": result.get("hiring_manager_score", 0),
            "tech_recruiter_score": result.get("tech_recruiter_score", 0),
            "match_score": result.get("avg_score", 0),
            "tailoring_model": tailoring_model,
            "matched_resume": resume_type,
        })
        result["job_id"] = job_id
        return result
    elif task_type == "cover_letter":
        resume_type = payload.get("resume_type", "sre_devops")
        resume_tex = _resumes.get(resume_type, "")
        result = _do_cover_letter(job, resume_tex, payload.get("company", "Unknown"), payload.get("job_title", "Software Engineer"))
        _update_job_artifacts(job_id, {"cover_letter_s3_url": result.get("pdf_url", "")})
        result["job_id"] = job_id
        return result
    elif task_type == "contacts":
        result = _do_contacts(job)
        if result.get("contacts"):
            import json as _json
            _update_job_artifacts(job_id, {"linkedin_contacts": _json.dumps(result["contacts"])})
        result["job_id"] = job_id
        return result
    elif task_type == "rebuild_sections":
        result = _do_rebuild_sections(payload.get("job_id", ""), payload.get("sections", {}), user_id)
        return result
    else:
        raise ValueError(f"Unknown task_type: {task_type}")


def _process_sqs_task(event, context):
    """Process SQS records (Lambda handler for the task queue).

    Uses the ReportBatchItemFailures pattern so only failed messages
    are retried rather than the entire batch.
    """
    # Ensure config / AI client / resumes are loaded (cold start)
    _initialize_state()

    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = json.loads(record["body"])
            task_id = body["task_id"]
            task_type = body["task_type"]

            # Load the full payload from Supabase
            task_row = None
            if _db:
                res = _db.client.table("pipeline_tasks").select("*").eq("task_id", task_id).maybe_single().execute()
                if res and res.data:
                    task_row = res.data

            if not task_row:
                logger.error("SQS task %s: no matching row in pipeline_tasks", task_id)
                batch_item_failures.append({"itemIdentifier": message_id})
                continue

            payload = task_row.get("payload") or {}
            user_id = task_row.get("user_id", "")

            result = _dispatch_task(task_type, payload, user_id=user_id)
            _save_task(task_id, user_id, {"status": "done", "result": result})

        except Exception as e:
            logger.error("SQS message %s failed: %s", message_id, e, exc_info=True)
            # Mark the task as errored in Supabase (best-effort)
            try:
                body = json.loads(record["body"])
                task_id = body.get("task_id", "")
                if task_id and _db:
                    res = _db.client.table("pipeline_tasks").select("user_id").eq("task_id", task_id).maybe_single().execute()
                    uid = res.data["user_id"] if res and res.data else ""
                    _save_task(task_id, uid, {"status": "error", "error": str(e)})
            except Exception:
                pass
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


# ---------------------------------------------------------------------------
# Synchronous worker functions (called from background threads)
# ---------------------------------------------------------------------------

def _do_tailor(job, base_tex, resume_type, company, job_title):
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = tailor_resume(job, base_tex, _ai_client, Path(tmpdir))
        if not tex_path or not Path(tex_path).exists():
            raise RuntimeError(f"Tailoring failed for {company} — AI returned empty result")
        tailored_tex = Path(tex_path).read_text()

        scoring_failed = False
        try:
            final_tex, scores = score_and_improve(tailored_tex, job, _ai_client)
        except Exception as e:
            logger.warning("score_and_improve failed: %s — using unscored resume", e)
            scores = {"ats_score": 0, "hiring_manager_score": 0, "tech_recruiter_score": 0}
            final_tex = tailored_tex
            scoring_failed = True

        final_tex_path = Path(tmpdir) / "final_resume.tex"
        final_tex_path.write_text(final_tex)
        pdf_path = compile_tex_to_pdf(str(final_tex_path), tmpdir)
        if not pdf_path:
            raise RuntimeError("LaTeX compilation failed")

        # Upload to S3
        import datetime
        date_str = datetime.date.today().isoformat()
        safe_name = f"{company}_{job_title}_resume.pdf".replace(" ", "_")
        bucket = os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt")
        s3_key = f"web/{date_str}/resumes/{safe_name}"
        pdf_url = s3_upload_file(pdf_path, s3_key, bucket) or ""

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)
        return {
            "ats_score": ats, "hiring_manager_score": hm,
            "tech_recruiter_score": tr, "avg_score": round((ats + hm + tr) / 3),
            "pdf_url": pdf_url,
            "scoring_failed": scoring_failed,
        }


def _do_cover_letter(job, resume_tex, company, job_title):
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = generate_cover_letter(job, resume_tex, _ai_client, Path(tmpdir))
        pdf_path = compile_tex_to_pdf(tex_path, tmpdir)
        if not pdf_path:
            raise RuntimeError("LaTeX compilation failed")

        import datetime
        date_str = datetime.date.today().isoformat()
        safe_name = f"{company}_{job_title}_cover_letter.pdf".replace(" ", "_")
        bucket = os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt")
        s3_key = f"web/{date_str}/cover_letters/{safe_name}"
        pdf_url = s3_upload_file(pdf_path, s3_key, bucket) or ""
        return {"pdf_url": pdf_url}


def _do_contacts(job):
    result = find_contacts(job, _ai_client)
    return {"contacts": result or []}


def _do_rebuild_sections(job_id: str, sections: dict, user_id: str) -> dict:
    """Rebuild a .tex from edited sections, compile to PDF, upload both to S3.

    Workflow:
    1. Fetch the current tailored .tex from S3 (to reuse its preamble)
    2. Rebuild the body from `sections` using parse_sections.rebuild_tex_from_sections
    3. Compile the new .tex with tectonic
    4. Upload updated .tex and .pdf to S3 (overwrite in place)
    5. Update the job row with the new resume_s3_url

    Returns dict with pdf_url and tex_s3_key.
    """
    from lambdas.pipeline.parse_sections import rebuild_tex_from_sections

    bucket = os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))
    tex_s3_key = f"users/{user_id}/resumes/{job_id}_tailored.tex"

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

    # Fetch base .tex (for preamble)
    try:
        obj = s3.get_object(Bucket=bucket, Key=tex_s3_key)
        base_tex = obj["Body"].read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Could not fetch base .tex from S3 ({tex_s3_key}): {e}")

    # Rebuild .tex from edited sections
    new_tex = rebuild_tex_from_sections(sections, base_tex)

    # Write new .tex back to S3
    s3.put_object(Bucket=bucket, Key=tex_s3_key, Body=new_tex.encode("utf-8"))

    # Compile to PDF
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / "resume.tex"
        tex_path.write_text(new_tex)
        pdf_path = compile_tex_to_pdf(str(tex_path), tmpdir)
        if not pdf_path:
            raise RuntimeError("LaTeX compilation failed after section rebuild")

        # Upload PDF — use the same key as the existing PDF (replace .tex → .pdf)
        pdf_s3_key = tex_s3_key.replace(".tex", ".pdf")
        with open(pdf_path, "rb") as f:
            s3.put_object(
                Bucket=bucket,
                Key=pdf_s3_key,
                Body=f.read(),
                ContentType="application/pdf",
            )

    # Generate a presigned URL for the new PDF (7-day expiry)
    try:
        pdf_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": pdf_s3_key},
            ExpiresIn=7 * 24 * 60 * 60,
        )
    except Exception:
        pdf_url = ""

    # Update job row in Supabase
    _update_job_artifacts(job_id, {"resume_s3_url": pdf_url})

    return {
        "job_id": job_id,
        "tex_s3_key": tex_s3_key,
        "pdf_s3_key": pdf_s3_key,
        "pdf_url": pdf_url,
    }


# ---------------------------------------------------------------------------
# POST endpoints — return 202 Accepted with task_id for long-running ops
# ---------------------------------------------------------------------------

@app.post("/api/tailor", status_code=202)
def tailor_job(req: TailorRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    task_id = str(uuid.uuid4())
    payload = {
        "job_description": req.job_description,
        "job_title": req.job_title,
        "company": req.company,
        "resume_type": req.resume_type,
    }
    _enqueue_task(task_id, user.id, "tailor", payload)
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="resume_tailor_started",
            properties={
                "resume_type": req.resume_type,
                "jd_length": len(req.job_description),
            },
        )
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.post("/api/cover-letter", status_code=202)
def cover_letter(req: CoverLetterRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    task_id = str(uuid.uuid4())
    payload = {
        "job_description": req.job_description,
        "job_title": req.job_title,
        "company": req.company,
        "resume_type": req.resume_type,
    }
    _enqueue_task(task_id, user.id, "cover_letter", payload)
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="cover_letter_started",
            properties={
                "resume_type": req.resume_type,
                "jd_length": len(req.job_description),
            },
        )
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.post("/api/contacts", status_code=202)
def contacts(req: ContactsRequest, user: AuthUser = Depends(get_current_user)):
    task_id = str(uuid.uuid4())
    payload = {
        "job_description": req.job_description,
        "job_title": req.job_title,
        "company": req.company,
    }
    _enqueue_task(task_id, user.id, "contacts", payload)
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="contacts_search_started",
            properties={"jd_length": len(req.job_description)},
        )
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


class GenerateEmailRequest(BaseModel):
    template: str = Field(..., description="cold_outreach | follow_up | thank_you")
    contact_name: Optional[str] = None


class GenerateEmailResponse(BaseModel):
    subject: str
    body: str


_EMAIL_TEMPLATE_NAMES = {"cold_outreach", "follow_up", "thank_you"}

_EMAIL_PROMPTS = {
    "cold_outreach": """You are a career strategist writing a cold outreach email on behalf of a software engineer.

Job: {job_title} at {company}
{contact_line}
Key skills the candidate matches: {key_matches}
Job description excerpt: {jd_excerpt}

Write a short, confident cold outreach email. Rules:
- Subject: specific to the role and company, max 10 words
- 3-4 short paragraphs, no fluff, no "I hope this email finds you well"
- Open with why you are reaching out to THIS company specifically
- 1 concrete proof point (metric or achievement) mapped to a JD requirement
- Close with a clear, low-friction ask (15-min call, not "any opportunities")
- Tone: peer-to-peer, not applicant-to-gatekeeper. Confident, not desperate.
- Do NOT use: "I am passionate about", "leverage", "synergy", "excited to apply"
- Sign off as: Utkarsh

Return ONLY a JSON object: {{"subject": "...", "body": "..."}}""",

    "follow_up": """You are a career strategist writing a follow-up email for a software engineer who applied to a job 7+ days ago.

Job: {job_title} at {company}
{contact_line}
Key skills the candidate matches: {key_matches}
Job description excerpt: {jd_excerpt}

Write a concise follow-up email. Rules:
- Subject: reference the application directly, max 10 words
- 2-3 short paragraphs
- Mention the application was submitted and express continued interest
- Add one new proof point or relevant context not in the original application
- Close with a gentle ask to confirm receipt or discuss next steps
- Tone: confident and direct, not apologetic or pushy
- Sign off as: Utkarsh

Return ONLY a JSON object: {{"subject": "...", "body": "..."}}""",

    "thank_you": """You are a career strategist writing a post-interview thank-you email for a software engineer.

Job: {job_title} at {company}
{contact_line}
Key skills the candidate matches: {key_matches}
Job description excerpt: {jd_excerpt}

Write a brief thank-you note. Rules:
- Subject: thank them and reference the role, max 10 words
- 2-3 short paragraphs
- Reference something specific discussed in the interview (use a placeholder if unknown: "[topic discussed]")
- Reinforce one key qualification that maps directly to their biggest stated need
- Close by expressing genuine enthusiasm and readiness to move forward
- Tone: warm but professional, not sycophantic
- Sign off as: Utkarsh

Return ONLY a JSON object: {{"subject": "...", "body": "..."}}""",
}


@app.post("/api/dashboard/jobs/{job_id}/generate-email", response_model=GenerateEmailResponse)
def generate_email_for_job(
    job_id: str,
    body: GenerateEmailRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Generate a personalized email draft using AI for a given job and template type."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    if body.template not in _EMAIL_TEMPLATE_NAMES:
        raise HTTPException(400, f"Invalid template. Must be one of: {sorted(_EMAIL_TEMPLATE_NAMES)}")
    if _ai_client is None:
        raise HTTPException(503, "AI client not configured")

    result = (
        _db.client.table("jobs")
        .select("title, company, description, key_matches")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")

    job_row = result.data
    job_title = job_row.get("title") or "Software Engineer"
    company = job_row.get("company") or "the company"
    description = job_row.get("description") or ""
    raw_matches = job_row.get("key_matches") or []

    # Normalize key_matches — may be a JSON string or already a list
    if isinstance(raw_matches, str):
        try:
            raw_matches = json.loads(raw_matches)
        except Exception:
            raw_matches = []
    key_matches_str = ", ".join(raw_matches[:8]) if raw_matches else "not available"

    jd_excerpt = description[:600].strip() if description else "not available"

    contact_line = f"Contact: {body.contact_name}" if body.contact_name else ""

    prompt = _EMAIL_PROMPTS[body.template].format(
        job_title=job_title,
        company=company,
        contact_line=contact_line,
        key_matches=key_matches_str,
        jd_excerpt=jd_excerpt,
    )

    try:
        raw_ai = _ai_client.complete(prompt, temperature=0.7)
    except Exception as e:
        logger.error("Email generation AI call failed: %s", e)
        raise HTTPException(500, f"AI call failed: {e}")

    # Parse JSON from AI response — handle markdown fences
    import re as _re
    json_text = raw_ai.strip()
    fence_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_text, _re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1)
    else:
        brace_match = _re.search(r"\{.*\}", json_text, _re.DOTALL)
        if brace_match:
            json_text = brace_match.group(0)

    try:
        parsed = json.loads(json_text)
        subject = str(parsed.get("subject", "")).strip()
        email_body = str(parsed.get("body", "")).strip()
    except Exception:
        logger.warning("Email AI response was not valid JSON: %s", raw_ai[:200])
        raise HTTPException(500, "AI returned an unexpected format. Please try again.")

    if not subject or not email_body:
        raise HTTPException(500, "AI returned empty subject or body. Please try again.")

    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="email_template_generated",
            properties={"template": body.template, "has_contact_name": bool(body.contact_name)},
        )
    return GenerateEmailResponse(subject=subject, body=email_body)


class SuggestRequest(BaseModel):
    section: str
    current_content: str = ""


@app.post("/api/dashboard/jobs/{job_id}/suggest")
def suggest_section_improvement(
    job_id: str,
    body: SuggestRequest,
    user: AuthUser = Depends(get_current_user),
):
    """AI-suggest improvements for a specific resume section based on JD analysis."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    if _ai_client is None:
        raise HTTPException(503, "AI client not configured")

    result = (
        _db.client.table("jobs")
        .select("title, company, description, key_matches")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")

    job = result.data
    description = job.get("description") or ""
    key_matches = job.get("key_matches") or []
    if isinstance(key_matches, str):
        try:
            key_matches = json.loads(key_matches)
        except Exception:
            key_matches = []

    prompt = f"""Improve this resume section for a {job.get('title', 'Software Engineer')} role at {job.get('company', 'the company')}.

JD KEYWORDS TO ADDRESS: {', '.join(key_matches[:10]) if key_matches else 'N/A'}
JD EXCERPT: {description[:800]}

CURRENT SECTION ({body.section}):
{body.current_content}

Return ONLY the improved section content. No explanations. Keep the same format (LaTeX if the input is LaTeX, plain text if plain text). Make it more specific, quantified, and aligned with the JD keywords. Do NOT fabricate experience."""

    try:
        suggestion = _ai_client.complete(prompt, temperature=0.3)
    except Exception as e:
        logger.error("Suggest AI call failed: %s", e)
        raise HTTPException(500, f"AI call failed: {e}")

    return {"suggestion": suggestion.strip(), "section": body.section}


@app.post("/api/dashboard/jobs/{job_id}/research")
def generate_company_research(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Generate AI-powered company research for interview prep."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    if _ai_client is None:
        raise HTTPException(503, "AI client not configured")

    result = (
        _db.client.table("jobs")
        .select("title, company, description, source, location")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")

    job = result.data
    prompt = f"""Research this company for a job interview. Return a JSON object with these fields:

- company_overview: 2-3 sentences about what the company does, their market position, and size
- engineering_culture: 2-3 sentences about their engineering practices, tech stack, team structure
- red_flags: array of 0-3 potential concerns (layoffs, bad reviews, funding issues). Empty array if none
- talking_points: array of 3-5 specific things to mention in an interview showing you researched them
- salary_range: estimated salary range for this role and location (e.g. "€70,000 - €95,000")

Company: {job.get('company', '')}
Role: {job.get('title', '')}
Location: {job.get('location', '')}
JD excerpt: {(job.get('description') or '')[:1500]}

Return ONLY valid JSON. No explanations."""

    try:
        raw = _ai_client.complete(prompt, temperature=0.3)
    except Exception as e:
        raise HTTPException(500, f"AI call failed: {e}")

    import re as _re
    json_text = raw.strip()
    fence_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_text, _re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1)
    else:
        brace_match = _re.search(r"\{.*\}", json_text, _re.DOTALL)
        if brace_match:
            json_text = brace_match.group(0)

    try:
        research = json.loads(json_text)
    except Exception:
        raise HTTPException(500, "AI returned invalid JSON")

    # Cache in DB
    try:
        _db.client.table("jobs").update({"company_research": json.dumps(research)}) \
            .eq("job_id", job_id).eq("user_id", user.id).execute()
    except Exception:
        pass  # non-critical

    return research


@app.post("/api/dashboard/jobs/{job_id}/interview-prep")
def generate_interview_prep(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Generate AI-powered interview preparation for a specific job."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    if _ai_client is None:
        raise HTTPException(503, "AI client not configured")

    result = (
        _db.client.table("jobs")
        .select("title, company, description, key_matches, gaps")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")

    job = result.data
    key_matches = job.get("key_matches") or []
    if isinstance(key_matches, str):
        try:
            key_matches = json.loads(key_matches)
        except Exception:
            key_matches = []
    gaps = job.get("gaps") or []
    if isinstance(gaps, str):
        try:
            gaps = json.loads(gaps)
        except Exception:
            gaps = []

    prompt = f"""Generate interview preparation for this role. The candidate is Utkarsh Singh, a software engineer with 3 years at Clover IT Services (SRE/backend, AWS, Python, React) and MSc Cloud Computing from ATU. Return a JSON object:

- star_stories: array of 3 STAR stories tailored to this role. Each has: question (likely interview question), situation, task, action, result. Use REAL experience from Clover IT Services — do NOT fabricate.
- technical_topics: array of 8-12 technical topics to review based on JD requirements
- behavioral_questions: array of 5-7 likely behavioral questions for this role
- company_specific: array of 3-5 company-specific things to prepare (product knowledge, recent news, team structure)

Role: {job.get('title', '')} at {job.get('company', '')}
Key matches: {', '.join(key_matches[:10])}
Gaps to address: {', '.join(gaps[:5])}
JD excerpt: {(job.get('description') or '')[:1500]}

Return ONLY valid JSON."""

    try:
        raw = _ai_client.complete(prompt, temperature=0.4)
    except Exception as e:
        raise HTTPException(500, f"AI call failed: {e}")

    import re as _re
    json_text = raw.strip()
    fence_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_text, _re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1)
    else:
        brace_match = _re.search(r"\{.*\}", json_text, _re.DOTALL)
        if brace_match:
            json_text = brace_match.group(0)

    try:
        prep = json.loads(json_text)
    except Exception:
        raise HTTPException(500, "AI returned invalid JSON")

    # Cache in DB
    try:
        _db.client.table("jobs").update({"interview_prep": json.dumps(prep)}) \
            .eq("job_id", job_id).eq("user_id", user.id).execute()
    except Exception:
        pass

    return prep


@app.post("/api/dashboard/jobs/{job_id}/find-contacts", status_code=202)
def find_contacts_for_job(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Find LinkedIn contacts for a specific job."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    job = _db.client.table("jobs").select("title, company, description").eq("job_id", job_id).eq("user_id", user.id).execute()
    if not job.data:
        raise HTTPException(404, "Job not found")
    j = job.data[0]
    task_id = str(uuid.uuid4())
    payload = {
        "job_description": j.get("description", ""),
        "job_title": j.get("title", ""),
        "company": j.get("company", ""),
        "job_id": job_id,
    }
    _enqueue_task(task_id, user.id, "contacts", payload)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.get("/api/profile", response_model=ProfileResponse)
def get_profile(user: AuthUser = Depends(get_current_user)):
    if _db is None:
        raise HTTPException(503, "Database not configured")

    row = _db.get_user(user.id)
    if row is None:
        # Auto-create user on first profile fetch (just-in-time provisioning)
        row = _db.create_user({"id": user.id, "email": user.email})

    return ProfileResponse(
        id=row["id"],
        email=row["email"],
        full_name=row.get("name"),
        phone=row.get("phone"),
        location=row.get("location"),
        github_url=row.get("github"),
        linkedin_url=row.get("linkedin"),
        website=row.get("website"),
        visa_status=row.get("visa_status"),
        work_authorizations=row.get("work_authorizations"),
        candidate_context=row.get("candidate_context"),
        plan=row.get("plan", "free"),
        created_at=row.get("created_at"),
        gdpr_consent_at=row.get("gdpr_consent_at"),
        salary_expectation_notes=row.get("salary_expectation_notes") or "",
        notice_period_text=row.get("notice_period_text") or "",
        onboarding_completed_at=row.get("onboarding_completed_at"),
    )


@app.put("/api/profile", response_model=ProfileResponse)
def update_profile(
    req: ProfileUpdateRequest, user: AuthUser = Depends(get_current_user)
):
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Ensure user row exists (JIT provisioning for first-time users)
    existing = _db.get_user(user.id)
    if existing is None:
        _db.create_user({"id": user.id, "email": user.email})

    # Normalize field names (frontend may use aliases)
    raw = req.model_dump(exclude_none=True)
    update_data = {}
    for k, v in raw.items():
        if k in ("full_name", "name"):
            update_data["name"] = v
        elif k == "linkedin_url":
            update_data["linkedin"] = v
        elif k == "github_url":
            update_data["github"] = v
        elif k in ("target_roles", "target_locations"):
            continue  # Not in DB schema yet
        elif k == "complete_onboarding":
            if v:
                from datetime import datetime, timezone
                update_data["onboarding_completed_at"] = datetime.now(timezone.utc).isoformat()
        else:
            update_data[k] = v
    if not update_data:
        raise HTTPException(400, "No fields to update")

    row = _db.update_user(user.id, update_data)
    if row is None:
        raise HTTPException(404, "User not found")
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="profile_updated",
            properties={"fields_updated": list(update_data.keys())},
        )
    return ProfileResponse(
        id=row["id"],
        email=row["email"],
        full_name=row.get("name"),
        phone=row.get("phone"),
        location=row.get("location"),
        github_url=row.get("github"),
        linkedin_url=row.get("linkedin"),
        website=row.get("website"),
        visa_status=row.get("visa_status"),
        work_authorizations=row.get("work_authorizations"),
        candidate_context=row.get("candidate_context"),
        plan=row.get("plan", "free"),
        created_at=row.get("created_at"),
        gdpr_consent_at=row.get("gdpr_consent_at"),
        salary_expectation_notes=row.get("salary_expectation_notes") or "",
        notice_period_text=row.get("notice_period_text") or "",
        onboarding_completed_at=row.get("onboarding_completed_at"),
    )


@app.put("/api/search-config")
def update_search_config(body: dict, user: AuthUser = Depends(get_current_user)):
    """Update user's search configuration (queries, locations, etc.)."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Ensure user row exists (FK constraint on user_search_configs)
    existing = _db.get_user(user.id)
    if existing is None:
        _db.create_user({"id": user.id, "email": user.email})

    # Map frontend field names to DB column names and filter unknown keys
    _FIELD_MAP = {
        "queries": "queries", "keywords": "queries", "job_titles": "queries",
        "search_queries": "queries",
        "locations": "locations",
        "geo_regions": "geo_regions",
        "experience_levels": "experience_levels", "experience_level": "experience_levels",
        "days_back": "days_back",
        "max_jobs_per_run": "max_jobs_per_run",
        "min_match_score": "min_match_score", "min_score": "min_match_score",
    }
    clean = {}
    for k, v in body.items():
        db_col = _FIELD_MAP.get(k)
        if db_col:
            clean[db_col] = v
    if not clean:
        raise HTTPException(400, f"No valid fields. Accepted: {list(_FIELD_MAP.keys())}")

    result = _db.upsert_search_config(user.id, clean)
    return result


@app.get("/api/search-config")
def get_search_config(user: AuthUser = Depends(get_current_user)):
    """Get user's search configuration."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    config = _db.get_search_config(user.id)
    return config or {}


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"New", "Applied", "Phone Screen", "Interview", "Offer", "Rejected", "Withdrawn", "Accepted"}


@app.get("/api/dashboard/jobs")
def get_dashboard_jobs(
    user: AuthUser = Depends(get_current_user),
    page: int = 1,
    per_page: int = 25,
    status: Optional[str] = None,
    min_score: Optional[float] = None,
    source: Optional[str] = None,
    company: Optional[str] = None,
    tailored: Optional[str] = None,
    tier: Optional[str] = None,
    hide_expired: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    archetype: Optional[str] = None,
    seniority: Optional[str] = None,
    remote: Optional[str] = None,
    level_fit: Optional[str] = None,
    skill: Optional[str] = None,
):
    """Paginated, filterable job list."""
    if _db is None:
        return {
            "jobs": [],
            "page": page,
            "per_page": per_page,
            "message": "Database not configured",
        }

    filters = {}
    if status:
        filters["status"] = status
    if min_score is not None:
        filters["min_score"] = min_score
    if source:
        filters["source"] = source
    if company:
        filters["company"] = company
    if tailored:
        filters["tailored"] = tailored
    if tier:
        filters["tier"] = tier
    if hide_expired and hide_expired.lower() == "true":
        filters["hide_expired"] = True
    if sort_by:
        filters["sort_by"] = sort_by
    if sort_order:
        filters["sort_order"] = sort_order
    if archetype:
        filters["archetype"] = archetype
    if seniority:
        filters["seniority"] = seniority
    if remote:
        filters["remote"] = remote
    if level_fit:
        filters["level_fit"] = level_fit
    if skill:
        filters["skill"] = skill

    jobs, total = _db.get_jobs(user.id, filters=filters, page=page, per_page=per_page)
    _refresh_s3_urls(jobs)
    return {"jobs": jobs, "page": page, "per_page": per_page, "total": total}


@app.patch("/api/dashboard/jobs/{job_id}")
def update_job(
    job_id: str,
    body: dict,
    user: AuthUser = Depends(get_current_user),
):
    """Update a job's fields (status, location, apply_url)."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    _EDITABLE_FIELDS = {"application_status", "location", "apply_url"}
    update_data = {k: v for k, v in body.items() if k in _EDITABLE_FIELDS and v is not None}

    if not update_data:
        raise HTTPException(400, f"At least one editable field required: {sorted(_EDITABLE_FIELDS)}")

    if "application_status" in update_data and update_data["application_status"] not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {sorted(_VALID_STATUSES)}")

    try:
        result = (
            _db.client.table("jobs")
            .update(update_data)
            .eq("job_id", job_id)
            .eq("user_id", user.id)
            .execute()
        )
        if not result.data:
            raise ValueError("Not found")
    except ValueError:
        raise HTTPException(404, "Job not found")
    if _posthog and "application_status" in update_data:
        _posthog.capture(
            distinct_id=user.id,
            event="job_status_updated",
            properties={"new_status": update_data["application_status"]},
        )
    return result.data[0]


@app.delete("/api/dashboard/jobs/{job_id}")
def delete_job(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Delete a job from the dashboard."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    try:
        _db.delete_job(user.id, job_id)
    except ValueError:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


@app.get("/api/dashboard/jobs/{job_id}")
def get_single_job(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Get a single job by ID."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    result = (
        _db.client.table("jobs")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")
    _refresh_s3_urls([result.data])
    return result.data


class TimelineEventRequest(BaseModel):
    status: str
    notes: Optional[str] = None


@app.get("/api/dashboard/jobs/{job_id}/timeline")
def get_job_timeline(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """List all timeline events for a job, newest first."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Verify the job belongs to this user before returning timeline data.
    job_check = (
        _db.client.table("jobs")
        .select("job_id")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not job_check or not job_check.data:
        raise HTTPException(404, "Job not found")

    result = (
        _db.client.table("application_timeline")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@app.post("/api/dashboard/jobs/{job_id}/timeline")
def add_timeline_event(
    job_id: str,
    body: TimelineEventRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Add a status-update event to the timeline and update jobs.application_status."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    if body.status not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {sorted(_VALID_STATUSES)}")

    # Verify the job belongs to this user.
    job_check = (
        _db.client.table("jobs")
        .select("job_id")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not job_check or not job_check.data:
        raise HTTPException(404, "Job not found")

    # Insert the timeline event.
    event = {
        "user_id": user.id,
        "job_id": job_id,
        "status": body.status,
        "notes": body.notes or None,
    }
    insert_result = (
        _db.client.table("application_timeline")
        .insert(event)
        .execute()
    )
    inserted = insert_result.data[0] if insert_result.data else event

    # Also keep jobs.application_status in sync.
    _db.client.table("jobs").update({"application_status": body.status}).eq("job_id", job_id).eq("user_id", user.id).execute()

    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="timeline_event_added",
            properties={"status": body.status, "has_notes": bool(body.notes)},
        )
    return inserted


# ---------------------------------------------------------------------------
# Resume Version History endpoints (Phase 3.3c)
# ---------------------------------------------------------------------------

@app.get("/api/dashboard/jobs/{job_id}/versions")
def get_resume_versions(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """List all saved resume versions for a job, newest first."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Verify the job belongs to this user.
    job_check = (
        _db.client.table("jobs")
        .select("job_id")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not job_check or not job_check.data:
        raise HTTPException(404, "Job not found")

    result = (
        _db.client.table("resume_versions")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .order("version_number", desc=True)
        .execute()
    )
    return result.data or []


@app.post("/api/dashboard/jobs/{job_id}/versions/{version_number}/restore", status_code=200)
def restore_resume_version(
    job_id: str,
    version_number: int,
    user: AuthUser = Depends(get_current_user),
):
    """Restore a saved version as the current resume/cover letter for a job."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Verify the job belongs to this user.
    job_check = (
        _db.client.table("jobs")
        .select("job_id, resume_s3_url, cover_letter_s3_url, tailoring_model, resume_version")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not job_check or not job_check.data:
        raise HTTPException(404, "Job not found")
    job = job_check.data

    # Fetch the requested version.
    ver_check = (
        _db.client.table("resume_versions")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .eq("version_number", version_number)
        .maybe_single()
        .execute()
    )
    if not ver_check or not ver_check.data:
        raise HTTPException(404, "Version not found")
    version = ver_check.data

    # Save the current live URLs as a new snapshot before overwriting.
    current_version = job.get("resume_version") or 1
    if job.get("resume_s3_url") or job.get("cover_letter_s3_url"):
        _db.client.table("resume_versions").insert({
            "user_id": user.id,
            "job_id": job_id,
            "version_number": current_version,
            "resume_s3_url": job.get("resume_s3_url"),
            "cover_letter_s3_url": job.get("cover_letter_s3_url"),
            "tailoring_model": job.get("tailoring_model"),
        }).execute()

    next_version = current_version + 1
    _db.client.table("jobs").update({
        "resume_s3_url": version.get("resume_s3_url"),
        "cover_letter_s3_url": version.get("cover_letter_s3_url"),
        "tailoring_model": version.get("tailoring_model"),
        "resume_version": next_version,
    }).eq("job_id", job_id).eq("user_id", user.id).execute()

    return {
        "resume_s3_url": version.get("resume_s3_url"),
        "cover_letter_s3_url": version.get("cover_letter_s3_url"),
        "tailoring_model": version.get("tailoring_model"),
        "resume_version": next_version,
    }


# ---------------------------------------------------------------------------
# Smart Section Editor endpoints (Phase 3.3b)
# ---------------------------------------------------------------------------

class SectionsBuildRequest(BaseModel):
    sections: dict


@app.get("/api/dashboard/jobs/{job_id}/sections")
def get_job_sections(
    job_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Parse the tailored .tex for a job into editable plain-text sections.

    Fetches the .tex from S3 using the key convention
    ``users/{user_id}/resumes/{job_hash}_tailored.tex``, parses it into
    structured sections, and runs JD keyword analysis.

    Returns:
        sections (dict), jd_analysis (dict), tex_s3_key (str)
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Fetch the job row to get description (for JD analysis)
    result = (
        _db.client.table("jobs")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(404, "Job not found")

    job_row = result.data
    jd = job_row.get("description", "") or ""

    # Derive tex S3 key from convention: users/{user_id}/resumes/{job_id}_tailored.tex
    bucket = os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))
    tex_s3_key = f"users/{user.id}/resumes/{job_id}_tailored.tex"

    # Fetch .tex from S3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    try:
        obj = s3.get_object(Bucket=bucket, Key=tex_s3_key)
        tex_content = obj["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        raise HTTPException(404, f"No tailored .tex found for job {job_id}. Run tailoring first.")
    except Exception as e:
        logger.error("S3 fetch failed for %s: %s", tex_s3_key, e)
        raise HTTPException(500, f"Could not retrieve .tex: {e}")

    # Parse into sections
    from lambdas.pipeline.parse_sections import (
        parse_resume_sections,
        analyze_sections_vs_jd,
    )
    sections = parse_resume_sections(tex_content)
    jd_analysis = analyze_sections_vs_jd(sections, jd) if jd else {}

    return {
        "sections": sections,
        "jd_analysis": jd_analysis,
        "tex_s3_key": tex_s3_key,
    }


@app.post("/api/dashboard/jobs/{job_id}/sections", status_code=202)
def update_job_sections(
    job_id: str,
    body: SectionsBuildRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Rebuild .tex from edited sections, compile to PDF, upload to S3.

    Takes the edited sections dict, fetches the original .tex from S3 to
    reuse its preamble, rebuilds the document body, compiles with tectonic,
    uploads both .tex and .pdf to S3, and updates the job row with the new
    PDF URL.

    Returns a task_id to poll via GET /api/tasks/{task_id}.
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Verify job ownership
    job_check = (
        _db.client.table("jobs")
        .select("job_id, description")
        .eq("job_id", job_id)
        .eq("user_id", user.id)
        .maybe_single()
        .execute()
    )
    if not job_check or not job_check.data:
        raise HTTPException(404, "Job not found")

    task_id = str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "sections": body.sections,
    }
    _enqueue_task(task_id, user.id, "rebuild_sections", payload)
    return {"task_id": task_id, "poll_url": f"/api/tasks/{task_id}"}


@app.get("/api/dashboard/skills")
def get_dashboard_skills(user: AuthUser = Depends(get_current_user)):
    """Return unique skills from key_matches across non-expired jobs, sorted by frequency."""
    if _db is None:
        return {"skills": []}

    from collections import Counter
    jobs = (
        _db.client.table("jobs")
        .select("key_matches")
        .eq("user_id", user.id)
        .eq("is_expired", False)
        .not_.is_("key_matches", "null")
        .execute()
    )

    counts: Counter = Counter()
    for j in jobs.data:
        for s in j.get("key_matches") or []:
            counts[s.strip()] += 1

    # Only return skills appearing in 3+ jobs
    skills = [
        {"name": name, "count": count}
        for name, count in counts.most_common()
        if count >= 3
    ]
    return {"skills": skills}


@app.get("/api/dashboard/stats")
def get_dashboard_stats(user: AuthUser = Depends(get_current_user)):
    """Aggregate metrics for the dashboard KPI cards."""
    if _db is None:
        return {
            "total_jobs": 0,
            "matched_jobs": 0,
            "avg_match_score": 0,
            "jobs_by_status": {},
            "message": "Database not configured",
        }

    return _db.get_job_stats(user.id)


@app.get("/api/dashboard/runs")
def get_dashboard_runs(user: AuthUser = Depends(get_current_user)):
    """Pipeline run history."""
    if _db is None:
        return {"runs": [], "message": "Database not configured"}

    # Auto-clean stale runs stuck in "running" for > 2 hours
    try:
        _db.cleanup_stale_runs(user.id)
    except Exception as e:
        logger.warning("Stale run cleanup failed: %s", e)

    return {"runs": _db.get_runs(user.id)}


# ---------------------------------------------------------------------------
# Pipeline trigger + status endpoints (Step Functions)
# ---------------------------------------------------------------------------

_sfn_client = None

def _get_sfn():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    return _sfn_client


class PipelineRunRequest(BaseModel):
    queries: list[str] = Field(default=["software engineer"], description="Search queries")


class SingleJobRunRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"
    resume_type: str = "sre_devops"


@app.post("/api/pipeline/run", status_code=202)
def run_pipeline(req: PipelineRunRequest, user: AuthUser = Depends(get_current_user)):
    """Start a daily pipeline execution via Step Functions."""
    daily_arn = os.environ.get("DAILY_PIPELINE_ARN")
    if not daily_arn:
        raise HTTPException(500, "Pipeline not configured")

    # Rate limit: max 5 runs per day, 1 concurrent
    if _db:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        runs = _db.get_runs(user.id)
        today_runs = [r for r in runs if r.get("run_date") == today]
        if len(today_runs) >= 5:
            raise HTTPException(429, "Maximum 5 pipeline runs per day")
        running = [r for r in runs if r.get("status") == "running"]
        if running:
            raise HTTPException(409, "A pipeline is already running")

    import hashlib
    query_hash = hashlib.md5("|".join(req.queries).encode()).hexdigest()[:12]

    sfn = _get_sfn()
    execution = sfn.start_execution(
        stateMachineArn=daily_arn,
        input=json.dumps({
            "user_id": user.id,
            "queries": req.queries,
            "query_hash": query_hash,
        }),
    )

    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="pipeline_started",
            properties={"query_count": len(req.queries)},
        )
    return {
        "executionArn": execution["executionArn"],
        "startDate": execution["startDate"].isoformat(),
        "pollUrl": f"/api/pipeline/status/{execution['executionArn'].split(':')[-1]}",
    }


@app.post("/api/pipeline/run-single", status_code=202)
def run_single_job(req: SingleJobRunRequest, user: AuthUser = Depends(get_current_user)):
    """Start a single-job pipeline (Add Job) via Step Functions."""
    single_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN")
    if not single_arn:
        raise HTTPException(500, "Pipeline not configured")

    sfn = _get_sfn()
    execution = sfn.start_execution(
        stateMachineArn=single_arn,
        input=json.dumps({
            "user_id": user.id,
            "job_description": req.job_description,
            "job_title": req.job_title,
            "company": req.company,
            "resume_type": req.resume_type,
            "skip_scoring": False,
        }),
    )

    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="pipeline_single_job_started",
            properties={
                "resume_type": req.resume_type,
                "jd_length": len(req.job_description),
            },
        )
    return {
        "executionArn": execution["executionArn"],
        "startDate": execution["startDate"].isoformat(),
        "pollUrl": f"/api/pipeline/status/{execution['executionArn'].split(':')[-1]}",
    }


class RetailorRequest(BaseModel):
    tier: str = "S"  # S, A, or SA for both
    max_jobs: int = 50


@app.post("/api/pipeline/re-tailor", status_code=202)
def re_tailor_jobs(req: RetailorRequest, user: AuthUser = Depends(get_current_user)):
    """Re-tailor existing jobs that are missing resumes.

    Finds S and/or A tier jobs without resume_s3_url and starts
    the single-job pipeline for each (up to max_jobs).
    This is the proper way to regenerate artifacts for existing jobs
    without running the full daily pipeline.
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")

    tiers = ["S", "A"] if req.tier == "SA" else [req.tier]
    jobs = (
        _db.client.table("jobs")
        .select("job_id, job_hash, match_score, score_tier")
        .eq("user_id", user.id)
        .in_("score_tier", tiers)
        .eq("is_expired", False)
        .is_("resume_s3_url", "null")
        .order("match_score", desc=True)
        .limit(req.max_jobs)
        .execute()
    )

    if not jobs.data:
        return {"queued": 0, "message": "No jobs need re-tailoring"}

    # Start single-job pipeline for each
    sfn = _get_sfn()
    single_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN")
    if not single_arn:
        raise HTTPException(500, "Single-job pipeline not configured")

    queued = 0
    errors = 0
    for job in jobs.data:
        try:
            sfn.start_execution(
                stateMachineArn=single_arn,
                name=f"retailor-{job['job_hash'][:12]}-{int(__import__('time').time())}",
                input=json.dumps({
                    "user_id": user.id,
                    "job_hash": job["job_hash"],
                    "job_id": job["job_id"],
                    "skip_scoring": True,
                }),
            )
            queued += 1
        except Exception as e:
            logger.warning(f"Failed to start re-tailor for {job['job_hash']}: {e}")
            errors += 1

    return {
        "queued": queued,
        "errors": errors,
        "tier": req.tier,
        "message": f"Started re-tailoring {queued} jobs via single-job pipeline",
    }


@app.get("/api/pipeline/status")
def pipeline_status(user: AuthUser = Depends(get_current_user)):
    """Get latest pipeline metrics and run status."""
    if _db is None:
        raise HTTPException(500, "Database not configured")

    runs = _db.get_runs(user.id)
    latest = runs[0] if runs else None

    # Fallback: query Step Functions for latest execution if pipeline_runs is empty
    if not latest:
        try:
            import boto3
            import json as _json
            sfn = boto3.client("states", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
            state_machine_arn = os.environ.get(
                "DAILY_PIPELINE_ARN",
                "arn:aws:states:eu-west-1:385017713886:stateMachine:naukribaba-daily-pipeline",
            )
            # Get last 5 executions, pick the most recent SUCCEEDED one (or last overall)
            executions = sfn.list_executions(
                stateMachineArn=state_machine_arn, maxResults=5
            ).get("executions", [])
            ex = None
            for e in executions:
                if e["status"] == "SUCCEEDED":
                    ex = e
                    break
            if not ex and executions:
                ex = executions[0]  # fallback to most recent regardless of status

            if ex:
                latest = {
                    "status": "completed" if ex["status"] == "SUCCEEDED" else ex["status"].lower(),
                    "started_at": ex["startDate"].isoformat(),
                    "run_date": ex["startDate"].strftime("%Y-%m-%d"),
                    "jobs_found": 0,
                    "jobs_matched": 0,
                }
                if ex.get("stopDate"):
                    latest["completed_at"] = ex["stopDate"].isoformat()

                # Try to extract job counts from execution output
                try:
                    detail = sfn.describe_execution(executionArn=ex["executionArn"])
                    if detail.get("output"):
                        output = _json.loads(detail["output"])
                        # Scraper results are in scraper_results array
                        scraper_results = output.get("scraper_results", [])
                        total_found = sum(
                            r.get("count", 0) for r in scraper_results if isinstance(r, dict)
                        )
                        # Score result has matched count
                        score_result = output.get("score_result", {})
                        total_matched = score_result.get("matched_count", 0)
                        latest["jobs_found"] = total_found
                        latest["jobs_matched"] = total_matched
                except Exception:
                    pass  # counts stay at 0 if we can't parse output
        except Exception as e:
            logger.warning("Failed to fetch Step Functions status: %s", e)

    # Get scraper-level metrics for today
    metrics = []
    try:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        result = _db.client.table("pipeline_metrics").select("*") \
            .eq("user_id", user.id) \
            .gte("run_date", today) \
            .order("created_at", desc=True) \
            .limit(20).execute()
        metrics = result.data or []
    except Exception as e:
        logger.warning("Failed to fetch metrics: %s", e)

    return {
        "latest_run": latest,
        "today_metrics": metrics,
    }


@app.get("/api/pipeline/status/{execution_name}")
def pipeline_execution_status(execution_name: str, user: AuthUser = Depends(get_current_user)):
    """Poll a specific Step Functions execution by name."""
    daily_arn = os.environ.get("DAILY_PIPELINE_ARN", "")
    single_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN", "")

    # Reconstruct full ARN from execution name (try both state machines)
    base_arn = daily_arn.rsplit(":", 1)[0] if daily_arn else ""
    execution_arn = f"{base_arn}:{execution_name}" if base_arn else ""

    sfn = _get_sfn()
    try:
        result = sfn.describe_execution(executionArn=execution_arn)
    except Exception:
        # Try single-job pipeline ARN
        base_arn = single_arn.rsplit(":", 1)[0] if single_arn else ""
        execution_arn = f"{base_arn}:{execution_name}" if base_arn else ""
        try:
            result = sfn.describe_execution(executionArn=execution_arn)
        except Exception as e:
            raise HTTPException(404, f"Execution not found: {execution_name}")

    output = None
    if result.get("output"):
        try:
            output = json.loads(result["output"])
        except (json.JSONDecodeError, TypeError):
            output = result["output"]

    return {
        "name": result.get("name"),
        "status": result["status"],  # RUNNING, SUCCEEDED, FAILED, TIMED_OUT, ABORTED
        "startDate": result["startDate"].isoformat(),
        "stopDate": result.get("stopDate", "").isoformat() if result.get("stopDate") else None,
        "output": output,
    }


@app.post("/api/pipeline/re-tailor/{job_id}", status_code=202)
def re_tailor_job(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Re-tailor a job with the latest resume version via Step Functions (or local fallback)."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Get the job
    job = _db.client.table("jobs").select("*").eq("job_id", job_id).eq("user_id", user.id).execute()
    if not job.data:
        raise HTTPException(404, "Job not found")
    job = job.data[0]

    # Save the current resume/cover letter as a version snapshot BEFORE re-tailoring.
    current_version = job.get("resume_version") or 1
    if job.get("resume_s3_url") or job.get("cover_letter_s3_url"):
        try:
            _db.client.table("resume_versions").insert({
                "user_id": user.id,
                "job_id": job_id,
                "version_number": current_version,
                "resume_s3_url": job.get("resume_s3_url"),
                "cover_letter_s3_url": job.get("cover_letter_s3_url"),
                "tailoring_model": job.get("tailoring_model"),
            }).execute()
        except Exception as e:
            logger.warning("Failed to save version snapshot for %s: %s", job_id, e)

    next_version = current_version + 1
    _db.client.table("jobs").update({
        "resume_version": next_version,
    }).eq("job_id", job_id).execute()

    single_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN")
    if single_arn:
        # Production path: invoke Step Functions
        sfn = _get_sfn()
        execution = sfn.start_execution(
            stateMachineArn=single_arn,
            input=json.dumps({
                "user_id": user.id,
                "job_hash": job.get("job_hash"),
                "skip_scoring": True,
                "job_id": job_id,
            }),
        )
        return {
            "executionArn": execution["executionArn"],
            "pollUrl": f"/api/pipeline/status/{execution['executionArn'].split(':')[-1]}",
            "resume_version": next_version,
        }
    else:
        # Local dev fallback: enqueue as an async task
        task_id = str(uuid.uuid4())
        _enqueue_task(task_id, user.id, "tailor", {
            "job_hash": job.get("job_hash"),
            "skip_scoring": True,
            "job_id": job_id,
        })
        return {
            "task_id": task_id,
            "poll_url": f"/api/tasks/{task_id}",
            "resume_version": next_version,
        }


class CompileLatexRequest(BaseModel):
    tex_source: str = Field(..., min_length=10)


@app.post("/api/compile-latex")
def compile_latex(req: CompileLatexRequest, user: AuthUser = Depends(get_current_user)):
    """Compile LaTeX source to PDF and return the binary."""
    try:
        pdf_path = compile_tex_to_pdf(req.tex_source)
        if not pdf_path or not Path(pdf_path).exists():
            raise HTTPException(500, "LaTeX compilation failed — no PDF produced")

        pdf_bytes = Path(pdf_path).read_bytes()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=output.pdf"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("LaTeX compile error: %s", e)
        raise HTTPException(500, f"Compilation error: {str(e)}")


# ---------------------------------------------------------------------------
# Resume CRUD endpoints
# ---------------------------------------------------------------------------


@app.post("/api/resumes/upload")
async def upload_resume(
    file: UploadFile = File(...),
    resume_key: str = "default",
    label: str = "",
    user: AuthUser = Depends(get_current_user),
):
    """Upload a PDF resume, extract text, parse sections, store in DB."""
    if not _db:
        raise HTTPException(503, "Database not configured")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(400, "File too large (max 10MB)")

    from resume_parser import extract_text_from_pdf, parse_resume_sections

    text = extract_text_from_pdf(contents)
    if not text:
        raise HTTPException(400, "Could not extract text from PDF")

    sections = parse_resume_sections(text, ai_client=_ai_client)

    resume_data = {
        "resume_key": resume_key,
        "label": label or file.filename,
        "tex_content": text,  # Store raw text for now
    }

    result = _db.upsert_resume(user.id, resume_data)

    # Auto-populate profile from parsed resume sections (best-effort)
    profile_updates = {}
    if sections.get("name"):
        profile_updates["name"] = sections["name"]
    if sections.get("phone"):
        profile_updates["phone"] = sections["phone"]
    if sections.get("location"):
        profile_updates["location"] = sections["location"]
    if sections.get("skills"):
        profile_updates["candidate_context"] = (
            sections["skills"] if isinstance(sections["skills"], str)
            else json.dumps(sections["skills"])
        )
    if profile_updates:
        try:
            _db.update_user(user.id, profile_updates)
            logger.info("Auto-updated profile from resume: %s", list(profile_updates.keys()))
        except Exception as e:
            logger.warning("Profile auto-update failed: %s", e)

    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="resume_uploaded",
            properties={
                "resume_key": resume_key,
                "has_label": bool(label),
                "text_length": len(text),
            },
        )
    return {
        "resume_id": result.get("id"),
        "sections": sections,
        "extracted_profile": {
            "name": sections.get("name", ""),
            "email": sections.get("email", ""),
            "phone": sections.get("phone", ""),
            "location": sections.get("location", ""),
            "skills": sections.get("skills", ""),
            "years_of_experience": sections.get("years_of_experience"),
        }
    }


@app.get("/api/resumes")
def list_resumes(user: AuthUser = Depends(get_current_user)):
    """List all resumes for the authenticated user."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    resumes = _db.get_resumes(user.id)
    return {"resumes": resumes}


@app.delete("/api/resumes/{resume_id}")
def delete_resume(resume_id: str, user: AuthUser = Depends(get_current_user)):
    """Delete a resume owned by the authenticated user."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    try:
        _db.delete_resume(resume_id, user.id)
    except ValueError:
        raise HTTPException(404, "Resume not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Feedback endpoints
# ---------------------------------------------------------------------------


@app.post("/api/feedback/flag-score")
def flag_score(req: FlagScoreRequest, user: AuthUser = Depends(get_current_user)):
    """User flags a score as inaccurate -- creates ground truth for self-improvement."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    try:
        _db.client.table("pipeline_adjustments").insert({
            "user_id": user.id,
            "adjustment_type": "quality_flag",
            "risk_level": "high",
            "status": "pending",
            "payload": {
                "job_id": req.job_id,
                "feedback_type": req.feedback_type,
                "expected_score": req.expected_score,
                "comment": req.comment,
            },
            "reason": f"User flagged score for job {req.job_id}: {req.feedback_type}",
        }).execute()
    except Exception as e:
        logger.error("Failed to record score feedback: %s", e)
        raise HTTPException(500, "Failed to record feedback")
    if _posthog:
        _posthog.capture(
            distinct_id=user.id,
            event="score_flagged",
            properties={
                "feedback_type": req.feedback_type,
                "has_expected_score": req.expected_score is not None,
            },
        )
    return {"status": "ok", "message": "Feedback recorded"}


# ---------------------------------------------------------------------------
# GDPR endpoints
# ---------------------------------------------------------------------------


@app.post("/api/gdpr/consent")
def gdpr_consent(user: AuthUser = Depends(get_current_user)):
    """Record GDPR consent timestamp."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    from gdpr import record_consent

    result = record_consent(_db, user.id)
    return {"status": "consent_recorded", "gdpr_consent_at": result.get("gdpr_consent_at")}


@app.get("/api/gdpr/export")
def gdpr_export(user: AuthUser = Depends(get_current_user)):
    """Export all user data as a ZIP file (GDPR Article 15)."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    from gdpr import export_user_data

    zip_bytes = export_user_data(_db, user.id)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=my_data_{user.id[:8]}.zip"},
    )


@app.delete("/api/gdpr/delete")
def gdpr_delete(user: AuthUser = Depends(get_current_user)):
    """Request account deletion (soft-delete, hard-delete after 30 days)."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    from gdpr import request_deletion

    result = request_deletion(_db, user.id)
    return {
        "status": "deletion_requested",
        "message": "Your account will be permanently deleted in 30 days. Contact support to cancel.",
        "gdpr_deletion_requested_at": result.get("gdpr_deletion_requested_at"),
    }


# ---------------------------------------------------------------------------
#  Auto-Apply endpoints (stubs — Plan 2/3 will implement)
# ---------------------------------------------------------------------------

@app.get("/api/apply/eligibility/{job_id}")
def apply_eligibility(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Per-job eligibility — no AI, no network calls to platforms."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.get("apply_url"):
        return {"eligible": False, "reason": "no_apply_url"}
    # NOTE: resume_s3_key is the implicit ≤B-tier gate — the tailoring pipeline
    # only writes it for S/A/B per pipeline policy. Do not remove this gate
    # without re-instating an explicit tier filter.
    if not job.get("resume_s3_key"):
        return {"eligible": False, "reason": "no_resume"}

    canonical = job.get("canonical_hash")
    if canonical:
        existing = (
            _db.client.table("applications")
            .select("id, status, submitted_at")
            .eq("user_id", user.id)
            .eq("canonical_hash", canonical)
            .not_.in_("status", ["unknown", "failed"])
            .execute()
        )
        if existing.data:
            return {
                "eligible": False,
                "reason": "already_applied",
                "application_id": existing.data[0]["id"],
                "applied_at": existing.data[0].get("submitted_at"),
            }

    missing = check_profile_completeness(_db.get_user(user.id))
    if missing:
        return {
            "eligible": False,
            "reason": "profile_incomplete",
            "missing_required_fields": missing,
        }

    return {
        "eligible": True,
        "platform": job.get("apply_platform"),
        "board_token": job.get("apply_board_token"),
        "posting_id": job.get("apply_posting_id"),
    }


@app.get("/api/apply/preview/{job_id}")
def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Apply preview snapshot. Plan 3a returns no AI answers; Plan 3b will
    populate `questions` (platform metadata) and `answers` (AI-generated)
    without changing this response shape."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    if not job.get("apply_url"):
        return {"eligible": False, "reason": "no_apply_url"}
    if not job.get("resume_s3_key"):
        return {"eligible": False, "reason": "no_resume"}

    canonical = job.get("canonical_hash")
    if canonical:
        existing = (
            _db.client.table("applications")
            .select("id, status, submitted_at")
            .eq("user_id", user.id)
            .eq("canonical_hash", canonical)
            .not_.in_("status", ["unknown", "failed"])
            .execute()
        )
        if existing.data:
            return {
                "eligible": False,
                "reason": "already_applied",
                "application_id": existing.data[0]["id"],
            }

    profile = _db.get_user(user.id) or {}
    missing = check_profile_completeness(profile)
    if missing:
        return {
            "eligible": False,
            "reason": "profile_incomplete",
            "missing_required_fields": missing,
        }

    return {
        "eligible": True,
        "job": {
            "job_id": job["job_id"],
            "title": job.get("title"),
            "company": job.get("company"),
            "apply_url": job.get("apply_url"),
            "platform": job.get("apply_platform"),
        },
        "profile": {k: profile.get(k) for k in (
            "first_name", "last_name", "email", "phone", "linkedin",
            "github", "website", "location", "visa_status",
        )},
        "resume": {
            "s3_key": job.get("resume_s3_key"),
            "version": job.get("resume_version", 1),
        },
        "questions": [],
        "answers": [],
        "answers_generated": False,
    }


class StartSessionRequest(BaseModel):
    job_id: str


class StartSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    ws_token: str       # FRONTEND-audience token
    status: str
    reused: bool = False


@app.post("/api/apply/start-session", response_model=StartSessionResponse)
def apply_start_session(
    req: StartSessionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Launch a Fargate Chrome task for applying to a job."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness
    import shared.browser_sessions as browser_sessions
    from shared.ws_auth import issue_ws_token

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(req.job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    profile = _db.get_user(user.id) or {}
    missing = check_profile_completeness(profile)
    if missing:
        raise HTTPException(412, f"profile_incomplete:{','.join(missing)}")

    existing = browser_sessions.find_active_session_for_user(user.id)
    if existing:
        if existing.get("current_job_id") != req.job_id:
            # Session already active for a different job — frontend must
            # explicitly stop it before starting a new one. Reusing across
            # jobs would mean the user watches a Fargate browser pointed at
            # the wrong apply_url.
            raise HTTPException(409, f"session_active_for_different_job:{existing.get('current_job_id')}")
        sid = existing["session_id"]
        return StartSessionResponse(
            session_id=sid,
            ws_url=os.environ.get("BROWSER_WS_URL", ""),
            ws_token=issue_ws_token(user_id=user.id, session_id=sid, role="frontend"),
            status=existing.get("status", "ready"),
            reused=True,
        )

    session_id = str(uuid.uuid4())
    frontend_token = issue_ws_token(user_id=user.id, session_id=session_id, role="frontend")
    browser_token = issue_ws_token(user_id=user.id, session_id=session_id, role="browser")

    subnet_ids = [s for s in os.environ.get("BROWSER_SUBNET_IDS", "").split(",") if s]
    if not subnet_ids:
        raise HTTPException(500, "BROWSER_SUBNET_IDS not configured")

    ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    result = ecs.run_task(
        cluster=os.environ["CLUSTER_ARN"],
        taskDefinition=os.environ["TASK_DEF"],
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [os.environ.get("SECURITY_GROUP", "")],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [{
                "name": "browser",
                "environment": [
                    {"name": "SESSION_ID", "value": session_id},
                    {"name": "USER_ID", "value": user.id},
                    {"name": "JOB_ID", "value": req.job_id},
                    {"name": "APPLY_URL", "value": job.get("apply_url", "")},
                    {"name": "PLATFORM", "value": job.get("apply_platform", "unknown")},
                    {"name": "WS_TOKEN", "value": browser_token},
                ],
            }],
        },
    )

    if result.get("failures") or not result.get("tasks"):
        logger.error("Fargate run_task failed: %s", result)
        raise HTTPException(503, "Failed to launch browser session")

    browser_sessions.create_session(
        session_id=session_id,
        user_id=user.id,
        job_id=req.job_id,
        platform=job.get("apply_platform", "unknown"),
        fargate_task_arn=result["tasks"][0]["taskArn"],
    )

    return StartSessionResponse(
        session_id=session_id,
        ws_url=os.environ.get("BROWSER_WS_URL", ""),
        ws_token=frontend_token,
        status="starting",
        reused=False,
    )


class StopSessionRequest(BaseModel):
    session_id: str


@app.post("/api/apply/stop-session")
def apply_stop_session(
    req: StopSessionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Stop a cloud browser session — ecs:StopTask + mark session ended."""
    import shared.browser_sessions as browser_sessions

    session = browser_sessions.get_session(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user.id:
        raise HTTPException(403, "Not your session")

    task_arn = session.get("fargate_task_arn")
    if task_arn:
        try:
            ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
            ecs.stop_task(
                cluster=os.environ["CLUSTER_ARN"],
                task=task_arn,
                reason="User ended session",
            )
        except Exception as e:
            logger.warning("ECS stop_task failed for %s: %s", task_arn, e)

    browser_sessions.update_status(req.session_id, "ended")
    return {"status": "stopped"}


class RecordApplicationRequest(BaseModel):
    session_id: str
    job_id: str
    confirmation_screenshot_key: Optional[str] = None
    form_fields_detected: int = 0
    form_fields_filled: int = 0


@app.post("/api/apply/record")
def apply_record(
    req: RecordApplicationRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Record a successful cloud-browser submission. Idempotent: if an
    active application for the same canonical_hash already exists, return
    the existing row instead of inserting."""
    from datetime import datetime, timezone
    from shared.load_job import load_job

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(req.job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    canonical = job.get("canonical_hash") or ""

    # Idempotency: only safe to consult when canonical_hash exists. Without
    # it, eq("canonical_hash", "") would collapse across every job that's
    # missing a hash for this user, returning a false-positive duplicate.
    if canonical:
        existing = (
            _db.client.table("applications")
            .select("id, status")
            .eq("user_id", user.id)
            .eq("canonical_hash", canonical)
            .not_.in_("status", ["unknown", "failed"])
            .execute()
        )
        if existing.data:
            return {
                "status": "recorded",
                "application_id": existing.data[0]["id"],
                "idempotent": True,
            }

    app_row = {
        "user_id": user.id,
        "job_id": req.job_id,
        "job_hash": job.get("job_hash", ""),
        "canonical_hash": canonical or None,
        "submission_method": "cloud_browser",
        "platform": job.get("apply_platform", "unknown"),
        "posting_id": job.get("apply_posting_id"),
        "board_token": job.get("apply_board_token"),
        "resume_s3_key": job.get("resume_s3_key", ""),
        "resume_version": job.get("resume_version", 1),
        "status": "submitted",
        "browser_session_id": req.session_id,
        "confirmation_screenshot_s3_key": req.confirmation_screenshot_key,
        "form_fields_detected": req.form_fields_detected,
        "form_fields_filled": req.form_fields_filled,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
    }
    result = _db.client.table("applications").insert(app_row).execute()
    application_id = (result.data or [{}])[0].get("id")

    if canonical:
        _db.client.table("jobs").update(
            {"application_status": "Applied"},
        ).eq("user_id", user.id).eq("canonical_hash", canonical).execute()

    _db.client.table("application_timeline").insert({
        "user_id": user.id,
        "job_id": req.job_id,
        "status": "Applied",
        "notes": f"Cloud browser via {job.get('apply_platform', 'unknown')}",
    }).execute()

    return {"status": "recorded", "application_id": application_id, "idempotent": False}


# ---------------------------------------------------------------------------
# Lambda handler (Mangum)
# ---------------------------------------------------------------------------

try:
    from mangum import Mangum
    _mangum = Mangum(app, api_gateway_base_path="/prod")
except ImportError:
    _mangum = None


def handler(event, context):
    """Route Lambda invocations: SQS events go to the task processor,
    everything else (API Gateway) goes to Mangum."""
    records = event.get("Records") if isinstance(event, dict) else None
    if records and any(r.get("eventSource") == "aws:sqs" for r in records):
        return _process_sqs_task(event, context)
    if _mangum:
        return _mangum(event, context)
    raise RuntimeError("Mangum is not installed — cannot handle API Gateway events")
