"""FastAPI backend for the Job Hunt landing page.

Exposes the pipeline's core AI modules as REST endpoints:
- POST /api/score       — score a JD against base resumes
- POST /api/tailor      — tailor resume + compile PDF, return Drive URL
- POST /api/cover-letter — generate cover letter PDF, return Drive URL
- POST /api/contacts    — find LinkedIn contacts + intro messages
- GET  /api/health      — health check
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

# Global state (initialized on startup)
_ai_client: Optional[AIClient] = None
_config: dict = {}
_resumes: dict[str, str] = {}  # {key: tex_content}


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
    global _ai_client, _config, _resumes
    _config = _load_config()
    _resumes = _load_resumes(_config)
    _ai_client = AIClient.from_config(_config)
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


@app.post("/api/score", response_model=ScoreResponse)
def score_job(req: ScoreRequest):
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
def tailor_job(req: TailorRequest):
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
def cover_letter(req: CoverLetterRequest):
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
def contacts(req: ContactsRequest):
    job = _Job(req.job_title, req.company, req.job_description)

    try:
        result = find_contacts(job, _ai_client)
    except Exception as e:
        logger.error("Contact finding failed: %s", e)
        raise HTTPException(500, f"Contact finding failed: {e}")

    return ContactsResponse(
        contacts=[Contact(**c) for c in result] if result else [],
    )


# ---------------------------------------------------------------------------
# Lambda handler (Mangum)
# ---------------------------------------------------------------------------

try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    handler = None
