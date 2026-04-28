"""Pydantic models per design spec §7.1.

These are the typed contract between the apply preview/submit endpoints
and the Plan 3c frontend. Do not change shapes without updating both.
"""
from __future__ import annotations

from typing import List, Literal, Optional, Union
from pydantic import BaseModel, Field


class PlatformInfo(BaseModel):
    """Parsed from an apply URL. None if URL is not a supported Easy Apply platform."""
    platform: Literal["greenhouse", "ashby"]
    board_token: str
    posting_id: str


class CustomQuestion(BaseModel):
    id: str
    label: str
    type: Literal["text", "textarea", "select", "multi_select",
                  "checkbox", "yes_no", "file"]
    required: bool
    options: Optional[List[str]] = None
    max_length: Optional[int] = None
    ai_answer: Union[str, bool, None] = None
    requires_user_action: bool = False
    # Spec §7.1 lists 5 categories assuming all questions go through AI.
    # Our answer_generator extends with "standard" (profile-pulled fields:
    # first_name, email, phone, etc.) and "file" (resume/cover_letter upload
    # markers). These bypass AI for efficiency. Frontend treats them the
    # same as "custom" for rendering — they just have ai_answer pre-populated.
    category: Literal["custom", "eeo", "confirmation",
                      "marketing", "referral", "standard", "file"] = "custom"


class ApplyPreviewResponse(BaseModel):
    eligible: bool
    reason: Optional[str] = None
    profile_complete: bool
    missing_required_fields: List[str] = Field(default_factory=list)
    job: dict
    platform: str
    platform_metadata: dict
    resume: dict
    profile: dict
    cover_letter: dict
    custom_questions: List[CustomQuestion] = Field(default_factory=list)
    already_applied: bool = False
    existing_application_id: Optional[str] = None
    cache_hit: bool = False


class CustomAnswer(BaseModel):
    question_id: str
    value: Union[str, bool, None]
    category: str
