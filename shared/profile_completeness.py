# shared/profile_completeness.py
"""Required-field check for the auto-apply flow (design §7.2)."""

from __future__ import annotations
from typing import Optional

REQUIRED_FIELDS = [
    "first_name", "last_name", "email", "phone", "linkedin",
    "visa_status", "work_authorizations",
    "default_referral_source", "notice_period_text",
]


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (dict, list, tuple)) and not value:
        return True
    return False


def check_profile_completeness(profile: Optional[dict]) -> list[str]:
    if profile is None:
        return list(REQUIRED_FIELDS)
    return [f for f in REQUIRED_FIELDS if _is_missing(profile.get(f))]
