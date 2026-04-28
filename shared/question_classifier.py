"""Categorize Greenhouse/Ashby application questions for AI-answer routing.

Categories drive different answer strategies in shared.answer_generator:

- eeo:          Decline to self-identify (or platform-specific 'prefer not to say')
- confirmation: Skip AI, set requires_user_action=True
- marketing:    Skip AI, set False / Unsubscribe
- referral:     Fuzzy-match user.default_referral_source against options
- custom:       Generate via AI council with cached temperature=0.3 prompt
"""
from __future__ import annotations

import re
from typing import Literal, Optional

Category = Literal["custom", "eeo", "confirmation", "marketing", "referral"]


_EEO_PATTERN = re.compile(
    r"\b(gender|ethnicity|race|veteran|disability|self.?identif(y|ication)|"
    r"sexual orientation|hispanic|latino|pronoun)\b",
    re.IGNORECASE,
)
_CONFIRMATION_PATTERN = re.compile(
    r"\b(confirm|certify|accurate|true.{0,20}information|understand|acknowledge|"
    r"i agree|i have read)\b",
    re.IGNORECASE,
)
_MARKETING_PATTERN = re.compile(
    r"\b(marketing|newsletter|subscribe|promotional|updates about|"
    r"opt.?in.{0,10}(email|news))\b",
    re.IGNORECASE,
)
_REFERRAL_PATTERN = re.compile(
    r"\b(how.{0,10}hear|referral source|source of awareness|"
    r"who referred|referred by)\b",
    re.IGNORECASE,
)


def classify_question(label: str, description: Optional[str] = None) -> Category:
    """Return the category for a question based on its label and optional description.

    Searches both label and description text. First matching pattern wins in this order:
    EEO → confirmation → marketing → referral → custom (default).
    """
    haystack = (label or "") + " " + (description or "")

    if _EEO_PATTERN.search(haystack):
        return "eeo"
    if _CONFIRMATION_PATTERN.search(haystack):
        return "confirmation"
    if _MARKETING_PATTERN.search(haystack):
        return "marketing"
    if _REFERRAL_PATTERN.search(haystack):
        return "referral"
    return "custom"
