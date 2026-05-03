# shared/profile_completeness.py
"""Required-field check for the auto-apply flow (design §7.2)."""

from __future__ import annotations
from typing import Optional

# NOTE: `default_referral_source` is required by shared/question_classifier.py
# for fuzzy-matching ATS referral fields, but there is currently NO UI that lets
# the user set it (not in Onboarding wizard, not in Settings, not in
# ProfileUpdateRequest in app.py). Until that UI lands, requiring it here made
# `profile_complete` structurally impossible for every user. Dropped from required
# until either the wizard collects it or the answer generator falls back gracefully.
# See backlog: "Add referral_source UI field" follow-up.
REQUIRED_FIELDS = [
    "first_name", "last_name", "email", "phone", "linkedin",
    "visa_status", "work_authorizations",
    "notice_period_text",
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
