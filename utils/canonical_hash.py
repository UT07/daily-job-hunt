"""Single canonical hash function for job deduplication.

Used everywhere a job hash is computed:
- lambdas/scrapers/normalizers.py
- scrapers/base.py
- lambdas/pipeline/merge_dedup.py

Formula: md5(normalize(company) \0 normalize(title) \0 normalize_ws(description))
"""
import hashlib
import re

_LEGAL_SUFFIXES = re.compile(
    r"\s+(?:Inc\.?|Ltd\.?|LLC|GmbH|Corp\.?|Co\.?|PLC|LP|LLP|SA|AG|BV|NV|SE)\s*$",
    re.IGNORECASE,
)


def normalize_company(company: str) -> str:
    """Lowercase, strip whitespace, remove common legal suffixes."""
    name = company.strip().lower()
    name = _LEGAL_SUFFIXES.sub("", name)
    return name.strip()


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs (spaces, tabs, newlines) to single space."""
    return re.sub(r"\s+", " ", text).strip()


def canonical_hash(company: str, title: str, description: str) -> str:
    """Return a 12-char hex hash for deduplicating jobs.

    Deterministic: same (company, title, description) always produces the same hash
    regardless of whitespace differences, casing, or legal suffixes on company name.
    """
    company = company or ""
    title = title or ""
    description = description or ""
    parts = "\0".join([
        normalize_company(company),
        normalize_whitespace(title).lower(),
        normalize_whitespace(description).lower(),
    ])
    return hashlib.md5(parts.encode()).hexdigest()[:12]
