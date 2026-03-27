"""FastAPI backend for the Job Hunt landing page.

Exposes the pipeline's core AI modules as REST endpoints:
- POST /api/score          — score a JD against base resumes
- POST /api/tailor         — tailor resume + compile PDF, return Drive URL
- POST /api/cover-letter   — generate cover letter PDF, return Drive URL
- POST /api/contacts       — find LinkedIn contacts + intro messages
- GET  /api/profile        — get authenticated user's profile
- PUT  /api/profile        — update authenticated user's profile
- POST /api/gdpr/consent   — record GDPR consent timestamp
- GET  /api/gdpr/export    — export all user data as ZIP (Article 15)
- DELETE /api/gdpr/delete  — request account deletion (soft-delete)
- GET  /api/dashboard/jobs  — paginated, filterable job list
- PATCH /api/dashboard/jobs/{job_id} — update a job's application status
- GET  /api/dashboard/stats — aggregate job metrics
- GET  /api/dashboard/runs  — pipeline run history
- POST /api/resumes/upload — upload a PDF resume, extract text, parse sections
- GET  /api/resumes        — list all resumes for the authenticated user
- DELETE /api/resumes/{id} — delete a resume
- GET  /api/templates      — list available resume templates (public)
- GET  /api/health         — health check (public)

All endpoints except /api/health and /api/templates require a valid Supabase JWT.
"""

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

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
    allow_origins=["*"],
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
        logger.warning("AI client init failed (endpoints needing AI will fail): %s", e)
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
    ats_score: int
    hiring_manager_score: int
    tech_recruiter_score: int
    avg_score: int
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
    drive_url: str


class CoverLetterRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"
    resume_type: str = "sre_devops"


class CoverLetterResponse(BaseModel):
    pdf_url: str
    drive_url: str


class ContactsRequest(BaseModel):
    job_description: str = Field(..., min_length=20)
    job_title: str = "Software Engineer"
    company: str = "Unknown"


class Contact(BaseModel):
    role: str
    why: str
    message: str
    search_url: str


class ContactsResponse(BaseModel):
    contacts: list[Contact]


class ProfileResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
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


def _upload_pdf_to_drive(pdf_path: str, filename: str) -> str:
    """Upload a PDF to Google Drive and return the shareable link."""
    drive_cfg = _config.get("google_drive", {})
    if not drive_cfg.get("enabled"):
        return ""
    try:
        from drive_uploader import _authenticate, _get_or_create_folder, _upload_file
        creds_path = drive_cfg.get("credentials_path", "google_credentials.json")
        # Lambda passes credentials as env var JSON; write to temp file if needed
        if not Path(creds_path).exists() and os.environ.get("GOOGLE_CREDENTIALS_JSON"):
            import json
            creds_path = "/tmp/google_credentials.json"
            with open(creds_path, "w") as f:
                f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
        service = _authenticate(creds_path)
        import datetime
        date_str = datetime.date.today().isoformat()
        root_id = drive_cfg.get("folder_id", "")
        parent_id = _get_or_create_folder(service, f"Job Hunt/{date_str}/web", root_id)
        url = _upload_file(service, pdf_path, parent_id,
                          share_with=drive_cfg.get("share_with", ""))
        return url
    except Exception as e:
        logger.error("Drive upload failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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


@app.post("/api/tailor", response_model=TailorResponse)
def tailor_job(req: TailorRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    job = _Job(req.job_title, req.company, req.job_description)
    base_tex = _resumes[req.resume_type]

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tex_path = tailor_resume(job, base_tex, _ai_client, Path(tmpdir))
        except Exception as e:
            logger.error("Tailoring failed: %s", e)
            raise HTTPException(500, f"Resume tailoring failed: {e}")

        # Score and improve
        tailored_tex = Path(tex_path).read_text()
        try:
            final_tex, scores = score_and_improve(tailored_tex, job, _ai_client)
        except Exception as e:
            logger.error("Scoring/improvement failed: %s", e)
            scores = {"ats_score": 0, "hiring_manager_score": 0, "tech_recruiter_score": 0}
            final_tex = tailored_tex

        # Write final tex and compile
        final_tex_path = Path(tmpdir) / "final_resume.tex"
        final_tex_path.write_text(final_tex)
        pdf_path = compile_tex_to_pdf(str(final_tex_path), tmpdir)

        if not pdf_path:
            raise HTTPException(500, "LaTeX compilation failed")

        # Upload to Drive
        safe_name = f"{req.company}_{req.job_title}_resume.pdf".replace(" ", "_")
        drive_url = _upload_pdf_to_drive(pdf_path, safe_name)

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)
        return TailorResponse(
            ats_score=ats,
            hiring_manager_score=hm,
            tech_recruiter_score=tr,
            avg_score=round((ats + hm + tr) / 3),
            pdf_url=pdf_path if not drive_url else "",
            drive_url=drive_url,
        )


@app.post("/api/cover-letter", response_model=CoverLetterResponse)
def cover_letter(req: CoverLetterRequest, user: AuthUser = Depends(get_current_user)):
    if req.resume_type not in _resumes:
        raise HTTPException(400, f"Unknown resume type: {req.resume_type}")

    job = _Job(req.job_title, req.company, req.job_description)
    resume_tex = _resumes[req.resume_type]

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            tex_path = generate_cover_letter(job, resume_tex, _ai_client, Path(tmpdir))
        except Exception as e:
            logger.error("Cover letter generation failed: %s", e)
            raise HTTPException(500, f"Cover letter generation failed: {e}")

        pdf_path = compile_tex_to_pdf(tex_path, tmpdir)
        if not pdf_path:
            raise HTTPException(500, "LaTeX compilation failed")

        safe_name = f"{req.company}_{req.job_title}_cover_letter.pdf".replace(" ", "_")
        drive_url = _upload_pdf_to_drive(pdf_path, safe_name)

        return CoverLetterResponse(
            pdf_url=pdf_path if not drive_url else "",
            drive_url=drive_url,
        )


@app.post("/api/contacts", response_model=ContactsResponse)
def contacts(req: ContactsRequest, user: AuthUser = Depends(get_current_user)):
    job = _Job(req.job_title, req.company, req.job_description)

    try:
        result = find_contacts(job, _ai_client)
    except Exception as e:
        logger.error("Contact finding failed: %s", e)
        raise HTTPException(500, f"Contact finding failed: {e}")

    return ContactsResponse(
        contacts=[Contact(**c) for c in result] if result else [],
    )


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
        full_name=row.get("full_name"),
        plan=row.get("plan", "free"),
        created_at=row.get("created_at"),
    )


@app.put("/api/profile", response_model=ProfileResponse)
def update_profile(
    req: ProfileUpdateRequest, user: AuthUser = Depends(get_current_user)
):
    if _db is None:
        raise HTTPException(503, "Database not configured")

    # Normalize field names (frontend may use aliases)
    raw = req.model_dump(exclude_none=True)
    update_data = {}
    for k, v in raw.items():
        if k == "full_name":
            update_data["name"] = v
        elif k == "linkedin_url":
            update_data["linkedin"] = v
        elif k == "github_url":
            update_data["github"] = v
        else:
            update_data[k] = v
    if not update_data:
        raise HTTPException(400, "No fields to update")

    row = _db.update_user(user.id, update_data)
    return ProfileResponse(
        id=row["id"],
        email=row["email"],
        full_name=row.get("full_name"),
        plan=row.get("plan", "free"),
        created_at=row.get("created_at"),
    )


@app.put("/api/search-config")
def update_search_config(body: dict, user: AuthUser = Depends(get_current_user)):
    """Update user's search configuration (queries, locations, etc.)."""
    if _db is None:
        raise HTTPException(503, "Database not configured")
    result = _db.upsert_search_config(user.id, body)
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

    jobs = _db.get_jobs(user.id, filters=filters, page=page, per_page=per_page)
    return {"jobs": jobs, "page": page, "per_page": per_page}


@app.patch("/api/dashboard/jobs/{job_id}")
def update_job_status(
    job_id: str,
    body: dict,
    user: AuthUser = Depends(get_current_user),
):
    """Update a job's application status."""
    if _db is None:
        raise HTTPException(503, "Database not configured")

    status = body.get("application_status")
    if not status:
        raise HTTPException(400, "application_status required")
    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {sorted(_VALID_STATUSES)}")

    result = _db.update_job_status(user.id, job_id, status)
    return result


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

    return {"runs": _db.get_runs(user.id)}


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

    import json

    resume_data = {
        "resume_key": resume_key,
        "label": label or file.filename,
        "tex_content": text,  # Store raw text for now
    }

    result = _db.upsert_resume(user.id, resume_data)
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
    """Delete a resume."""
    if not _db:
        raise HTTPException(503, "Database not configured")
    _db.delete_resume(resume_id)
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
    handler = Mangum(app)
except ImportError:
    handler = None
