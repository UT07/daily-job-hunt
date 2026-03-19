"""Base scraper interface and shared Job data model."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from abc import ABC, abstractmethod
from typing import List, Optional
import hashlib, json


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

    # S3 URLs for uploaded artifacts
    resume_s3_url: str = ""
    cover_letter_s3_url: str = ""

    # LinkedIn contacts for networking
    linkedin_contacts: str = ""  # JSON string of contacts list

    # Application tracking
    applied: str = "No"
    application_status: str = "New"  # New, Applied, Interview, Offer, Rejected, Withdrawn

    def __post_init__(self):
        if not self.job_id:
            raw = f"{self.title}|{self.company}|{self.location}|{self.source}"
            self.job_id = hashlib.md5(raw.encode()).hexdigest()[:12]

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
        seen = set()
        unique = []
        for job in jobs:
            key = f"{job.title.lower().strip()}|{job.company.lower().strip()}"
            if key not in seen:
                seen.add(key)
                unique.append(job)
        return unique
