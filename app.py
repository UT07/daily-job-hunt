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
- GET  /api/dashboard/stats         — aggregate metrics
- GET  /api/dashboard/runs          — run history
- POST /api/resumes/upload          — upload PDF resume
- GET  /api/resumes                 — list resumes
- DELETE /api/resumes/{id}          — delete resume
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Job Hunt API", version="1.0.0")

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


@app.on_event("startup")
def startup():
    global _ai_client, _config, _resumes, _db
    _config = _load_config()
    _resumes = _load_resumes(_config)
    try:
        _ai_client = AIClient.from_config(_config)
    except Exception as e:
        import traceback; logger.error("AI client init failed: %s\n%s", e, traceback.format_exc())
        _ai_client = None
    try:
        _db = SupabaseClient.from_env()
    except RuntimeError:
        logger.warning("Supabase not configured — profile endpoints will fail")
        _db = None
    # Wire up audit middleware with the DB client (no-ops gracefully if _db is None)
    set_audit_db(_db)
    logger.info("API started — %d resumes loaded, AI client ready", len(_resumes))


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

def _find_or_create_job(user_id: str, payload: dict) -> str:
    """Check if job exists in Supabase by company+title. Create if not. Return job_id."""
    if not _db:
        return ""
    company = payload.get("company", "Unknown")
    title = payload.get("job_title", "Software Engineer")
    description = payload.get("job_description", "")
    # Check for existing job with same company+title for this user
    try:
        result = _db.client.table("jobs").select("job_id,description").eq("user_id", user_id).ilike("company", company).ilike("title", title).execute()
        if result.data:
            # Compare descriptions — same company+title but different JD = different job
            from difflib import SequenceMatcher
            for existing in result.data:
                existing_desc = existing.get("description") or ""
                if not existing_desc or not description:
                    # If either has no description, match on company+title alone
                    return existing["job_id"]
                similarity = SequenceMatcher(None, description[:500], existing_desc[:500]).ratio()
                if similarity > 0.6:
                    return existing["job_id"]
            # All matches had different JDs — this is a new job
    except Exception as e:
        logger.warning(f"Job lookup failed: {e}")

    # Create new job entry
    import datetime
    import hashlib
    job_id = hashlib.md5(f"{company}:{title}:{user_id}".encode()).hexdigest()[:12]
    row = {
        "job_id": job_id,
        "user_id": user_id,
        "title": title,
        "company": company,
        "description": payload.get("job_description", ""),
        "source": "manual",
        "application_status": "New",
        "first_seen": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        _db.client.table("jobs").insert(row).execute()
    except Exception as e:
        logger.warning(f"Job creation failed: {e}")
    return job_id


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

    # Find or create job in dashboard
    job_id = _find_or_create_job(user_id, payload) if user_id else ""

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
    else:
        raise ValueError(f"Unknown task_type: {task_type}")


def _process_sqs_task(event, context):
    """Process SQS records (Lambda handler for the task queue).

    Uses the ReportBatchItemFailures pattern so only failed messages
    are retried rather than the entire batch.
    """
    # Ensure config / AI client / resumes are loaded (cold start)
    startup()

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
        else:
            update_data[k] = v
    if not update_data:
        raise HTTPException(400, "No fields to update")

    row = _db.update_user(user.id, update_data)
    if row is None:
        raise HTTPException(404, "User not found")
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

_VALID_STATUSES = {"New", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"}


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

    jobs, total = _db.get_jobs(user.id, filters=filters, page=page, per_page=per_page)
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
    return result.data


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
        }),
    )

    return {
        "executionArn": execution["executionArn"],
        "startDate": execution["startDate"].isoformat(),
        "pollUrl": f"/api/pipeline/status/{execution['executionArn'].split(':')[-1]}",
    }


@app.get("/api/pipeline/status")
def pipeline_status(user: AuthUser = Depends(get_current_user)):
    """Get latest pipeline metrics and run status."""
    if _db is None:
        raise HTTPException(500, "Database not configured")

    runs = _db.get_runs(user.id)
    latest = runs[0] if runs else None

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
    """Re-tailor a job with the latest resume version via Step Functions."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    single_arn = os.environ.get("SINGLE_JOB_PIPELINE_ARN")
    if not single_arn:
        raise HTTPException(500, "Pipeline not configured")

    # Get the job
    job = _db.client.table("jobs").select("*").eq("job_id", job_id).eq("user_id", user.id).execute()
    if not job.data:
        raise HTTPException(404, "Job not found")
    job = job.data[0]

    # Get current resume version
    resumes = _db.get_resumes(user.id)
    current_version = len(resumes) if resumes else 0

    sfn = _get_sfn()
    execution = sfn.start_execution(
        stateMachineArn=single_arn,
        input=json.dumps({
            "user_id": user.id,
            "job_description": job.get("description", ""),
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "resume_type": "default",
            "re_tailor": True,
            "job_id": job_id,
        }),
    )

    # Increment resume_version on the job
    _db.client.table("jobs").update({
        "resume_version": (job.get("resume_version") or 0) + 1,
    }).eq("job_id", job_id).execute()

    return {
        "executionArn": execution["executionArn"],
        "pollUrl": f"/api/pipeline/status/{execution['executionArn'].split(':')[-1]}",
        "resume_version": (job.get("resume_version") or 0) + 1,
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
    if sections.get("skills"):
        profile_updates["candidate_context"] = sections["skills"]
    if profile_updates:
        try:
            _db.update_user(user.id, profile_updates)
            logger.info("Auto-updated profile from resume: %s", list(profile_updates.keys()))
        except Exception as e:
            logger.warning("Profile auto-update failed: %s", e)

    return {"resume_id": result.get("id"), "sections": sections}


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
