"""URL → ATS platform classifier (informational only, never raises, never gates).

Used by:
- lambdas/pipeline/score_batch.py for new jobs at scoring-time
- scripts/backfill_apply_platform.py for one-shot historical backfill

Returns one of {greenhouse, lever, workday, ashby, smartrecruiters, workable,
taleo, icims, personio, linkedin_easy_apply} or None for unmatched URLs.

The /api/apply/* endpoints DO NOT gate on this column — auto-apply works for
jobs with apply_platform=None (cloud browser handles unknown forms via AI vision).
The actual gate is `apply_url`-non-null + `resume_s3_key`-non-null (latter
implicitly enforces ≤B-tier since the tailoring pipeline only writes
resume_s3_key for S/A/B-tier jobs per pipeline policy).
"""
from __future__ import annotations

import re
from typing import Optional


_PATTERNS = [
    ("greenhouse",          re.compile(r"boards\.greenhouse\.io/", re.IGNORECASE)),
    ("lever",               re.compile(r"jobs\.lever\.co/", re.IGNORECASE)),
    ("workday",             re.compile(r"\.myworkdayjobs\.com/", re.IGNORECASE)),
    ("ashby",               re.compile(r"jobs\.ashbyhq\.com/", re.IGNORECASE)),
    ("smartrecruiters",     re.compile(r"jobs\.smartrecruiters\.com/", re.IGNORECASE)),
    ("workable",            re.compile(r"apply\.workable\.com/", re.IGNORECASE)),
    ("taleo",               re.compile(r"\.taleo\.net/", re.IGNORECASE)),
    ("icims",               re.compile(r"\.icims\.com/", re.IGNORECASE)),
    ("personio",            re.compile(r"\.jobs\.personio\.(com|de|eu)/", re.IGNORECASE)),
    ("linkedin_easy_apply", re.compile(r"linkedin\.com/jobs/.*easy.?apply", re.IGNORECASE)),
]


def classify_apply_platform(url: Optional[str]) -> Optional[str]:
    """Return one of the supported platform names, or None.

    Pure function. Never raises. Treats non-string input as unknown.
    """
    if not url or not isinstance(url, str):
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(url):
            return name
    return None


_GREENHOUSE_STANDARD = re.compile(
    r"boards\.greenhouse\.io/(?P<board>[^/?]+)/jobs/(?P<posting>\d+)",
    re.IGNORECASE,
)
_GREENHOUSE_EMBED_PATH = re.compile(r"boards\.greenhouse\.io/embed/job_app", re.IGNORECASE)
_GREENHOUSE_EMBED_FOR = re.compile(r"[?&]for=(?P<board>[^&#]+)", re.IGNORECASE)
_GREENHOUSE_EMBED_TOKEN = re.compile(r"[?&]token=(?P<posting>\d+)", re.IGNORECASE)
_ASHBY_STANDARD = re.compile(
    r"jobs\.ashbyhq\.com/(?P<board>[^/?]+)/(?P<posting>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def extract_platform_ids(url: Optional[str]) -> Optional[dict]:
    """Extract platform-specific identifiers needed to call platform APIs.

    Returns a dict {platform, board_token, posting_id} or None if the URL
    isn't a recognized greenhouse/ashby URL or doesn't contain the slugs.

    Pure function. Never raises.
    """
    if not url or not isinstance(url, str):
        return None

    m = _GREENHOUSE_STANDARD.search(url)
    if m:
        return {
            "platform": "greenhouse",
            "board_token": m.group("board"),
            "posting_id": m.group("posting"),
        }

    if _GREENHOUSE_EMBED_PATH.search(url):
        m_board = _GREENHOUSE_EMBED_FOR.search(url)
        m_posting = _GREENHOUSE_EMBED_TOKEN.search(url)
        if m_board and m_posting:
            return {
                "platform": "greenhouse",
                "board_token": m_board.group("board"),
                "posting_id": m_posting.group("posting"),
            }

    m = _ASHBY_STANDARD.search(url)
    if m:
        return {
            "platform": "ashby",
            "board_token": m.group("board"),
            "posting_id": m.group("posting"),
        }

    return None
