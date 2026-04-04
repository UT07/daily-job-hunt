"""Base scraper interface and shared Job data model."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from abc import ABC, abstractmethod
from typing import List, Optional
import json

from utils.canonical_hash import canonical_hash


@dataclass
class Job:
    """Normalized job listing from any source."""
    title: str
    company: str
    location: str
    description: str
    apply_url: str
    source: str  # e.g. "serpapi", "jsearch", "adzuna"
    posted_date: Optional[str] = None
    salary: Optional[str] = None
    job_type: Optional[str] = None  # full-time, contract, etc.
    experience_level: Optional[str] = None
    remote: bool = False
    job_id: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Fields populated after matching/processing
    match_score: float = 0.0
    match_reasoning: str = ""
    matched_resume: str = ""  # "sre_devops" or "fullstack"
    tailored_tex_path: str = ""
    tailored_pdf_path: str = ""
    cover_letter_tex_path: str = ""
    cover_letter_pdf_path: str = ""

    # 3-score validation (populated before tailoring)
    ats_score: float = 0.0
    hiring_manager_score: float = 0.0
    tech_recruiter_score: float = 0.0

    # Initial match scores (before tailoring overwrites them)
    initial_match_score: float = 0.0
    initial_ats_score: float = 0.0
    initial_hm_score: float = 0.0
    initial_tr_score: float = 0.0

    # S3 URLs for uploaded artifacts
    resume_s3_url: str = ""
    cover_letter_s3_url: str = ""

    # Google Drive URLs for uploaded artifacts
    resume_drive_url: str = ""
    cover_letter_drive_url: str = ""

    # Google Docs URLs (editable docs, not just Drive file links)
    resume_doc_url: str = ""
    cover_letter_doc_url: str = ""

    # AI provenance — which model generated each artifact
    match_provider: str = ""
    match_model: str = ""
    tailoring_provider: str = ""
    tailoring_model: str = ""
    cover_letter_provider: str = ""
    cover_letter_model: str = ""

    # LinkedIn contacts for networking
    linkedin_contacts: str = ""  # JSON string of contacts list

    # Application tracking
    applied: str = "No"
    application_status: str = "New"  # New, Applied, Interview, Offer, Rejected, Withdrawn

    def __post_init__(self):
        if not self.job_id:
            self.job_id = canonical_hash(self.company, self.title, self.description or "")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Job":
        return Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})


class BaseScraper(ABC):
    """Interface that all job scrapers must implement."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, location: str, days_back: int = 1, **kwargs) -> List[Job]:
        """Search for jobs and return normalized Job objects."""
        ...

    def deduplicate(self, jobs: List[Job]) -> List[Job]:
        seen = {}
        for job in jobs:
            h = canonical_hash(job.company, job.title, job.description or "")
            if h in seen:
                # Keep the version with the longer description
                if len(job.description or "") > len(seen[h].description or ""):
                    seen[h] = job
            else:
                seen[h] = job
        return list(seen.values())
