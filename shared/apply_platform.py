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
    ("personio",            re.compile(r"\.jobs\.personio\.com/", re.IGNORECASE)),
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
