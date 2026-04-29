"""Geography- and work-auth-aware score capping.

Production scoring (lambdas/pipeline/score_batch.py) doesn't include user
profile context — it scores purely on resume↔JD fit. So Dublin-, UK-, and
US-based jobs all score the same on skill match. But for a Dublin-based
candidate authorized for IE only:
- Dublin/IE jobs that match skills+experience → S-tier appropriate
- Non-IE jobs (even if employer sponsors) → A-tier max (Dublin preferred)
- Non-IE jobs requiring sponsorship the user can't get → B-tier max

This module applies deterministic post-score caps to encode that hierarchy
without changing the AI scoring path. Cheap, no AI calls, no cache invalidation.

The fuller fix is to bake work-auth into the AI scoring prompt itself (see
backlog_work_auth_scoring.md). Cap applies regardless and is the defense-
in-depth layer.

Usage:
    from shared.work_auth import apply_geo_score_cap
    score_result = score_single_job_deterministic(job, resume_tex)
    score_result = apply_geo_score_cap(score_result, job, user_work_auth)
"""
from __future__ import annotations

from typing import Optional


# Single-tenant default. Multi-tenant version should derive from user profile
# (location → ISO country, or a dedicated home_country field).
DEFAULT_HOME_COUNTRY = "IE"

# Cap match_score (and the 3 perspective scores) at this value when the job
# is in a country other than the user's home country. 89 = max of A-tier band
# (S-tier starts at 90 in score_to_tier()).
NON_HOME_COUNTRY_CAP = 89

# Cap further when the user requires sponsorship for the job's country AND
# the JD doesn't show sponsor signals. 70 = max of B-tier band.
SPONSOR_REQUIRED_CAP = 70

# Softer cap when the JD has sponsorship-adjacent language ("relocation",
# "international candidates") that's a weaker signal than explicit "H1B".
# Still demotes from S to A or A to B but preserves more of the score.
SPONSOR_SOFT_CAP = 80

# Substrings (lowercase) that, if found in job description, indicate the
# employer explicitly sponsors visas. ANY match disables the cap entirely.
_HARD_SPONSOR_SIGNALS = (
    "h1b", "h-1b", "h1-b",
    "visa sponsorship",
    "sponsorship available",
    "sponsorship offered",
    "will sponsor",
    "we sponsor",
    "able to sponsor",
    "open to sponsoring",
    "sponsor work visa",
    "sponsor visa",
    "tn visa",
    "o-1 visa", "o1 visa",
)

# Softer signals — applies SPONSOR_SOFT_CAP instead of full SPONSOR_REQUIRED_CAP.
_SOFT_SPONSOR_SIGNALS = (
    "relocation",
    "international candidates",
    "open to relocating",
    "global team",
    "remote international",
)

# Location string substrings (lowercase) → ISO country code that the user's
# work_authorizations dict is keyed by. Order matters: longer/more-specific
# strings first to avoid false matches (e.g. "us-remote" before "us").
_LOCATION_TO_COUNTRY = [
    # United States — many forms
    ("united states", "US"),
    ("usa", "US"),
    ("u.s.a", "US"),
    ("u.s.", "US"),
    # State names that almost always imply US (avoiding ambiguous ones like "GA")
    ("california", "US"), ("new york", "US"), ("texas", "US"),
    ("washington state", "US"), ("massachusetts", "US"), ("colorado", "US"),
    ("illinois", "US"), ("virginia", "US"), ("georgia, ", "US"),
    ("north carolina", "US"), ("oregon", "US"), ("florida", "US"),
    # US cities that strongly imply US
    ("san francisco", "US"), ("new york city", "US"), ("nyc", "US"),
    ("los angeles", "US"), ("seattle", "US"), ("boston", "US"),
    ("chicago", "US"), ("austin", "US"), ("denver", "US"),
    # Country names — generic
    ("united kingdom", "UK"), ("uk,", "UK"), (", uk", "UK"),
    ("london", "UK"),
    ("canada", "CA"), ("toronto", "CA"), ("vancouver", "CA"),
    ("ireland", "IE"), ("dublin", "IE"),
    ("germany", "DE"), ("berlin", "DE"), ("munich", "DE"),
    ("france", "FR"), ("paris", "FR"),
    ("netherlands", "NL"), ("amsterdam", "NL"),
    ("singapore", "SG"),
    ("australia", "AU"), ("sydney", "AU"), ("melbourne", "AU"),
    ("india", "IN"), ("bangalore", "IN"), ("bengaluru", "IN"), ("mumbai", "IN"),
]

# Ambiguous location strings → no country attribution (skip cap to preserve recall).
_AMBIGUOUS_LOCATIONS = (
    "remote", "anywhere", "worldwide", "global", "emea", "americas",
    "apac", "north america", "europe", "europe / americas",
)


def _detect_country(location: Optional[str]) -> Optional[str]:
    """Map a free-text location string to an ISO country code, or None.

    Returns None for ambiguous locations ("Remote", "EMEA") to preserve
    recall — only cap when we're confident about the country.

    Country-name detection runs FIRST: "Remote, USA" should resolve to US,
    not be filtered as ambiguous. Pure-ambiguity ("Remote", "EMEA" alone)
    falls through to the ambiguous check.
    """
    if not location or not isinstance(location, str):
        return None
    lo = location.lower().strip()
    if not lo:
        return None
    # Try country-name match first; "Remote, USA" wins over ambiguous "remote"
    for needle, country in _LOCATION_TO_COUNTRY:
        if needle in lo:
            return country
    # No country detected — bail out for purely ambiguous strings
    for ambiguous in _AMBIGUOUS_LOCATIONS:
        if lo == ambiguous or lo.startswith(f"{ambiguous},"):
            return None
    return None


def _has_hard_sponsor_signal(description: Optional[str]) -> bool:
    if not description:
        return False
    lo = description.lower()
    return any(sig in lo for sig in _HARD_SPONSOR_SIGNALS)


def _has_soft_sponsor_signal(description: Optional[str]) -> bool:
    if not description:
        return False
    lo = description.lower()
    return any(sig in lo for sig in _SOFT_SPONSOR_SIGNALS)


_AUTHORIZED_TOKENS = (
    "stamp1g", "stamp 1g", "stamp4", "stamp 4", "stamp1",
    "authorized", "citizen", "permanent resident", "pr ",
    "green card", "indefinite leave", "ilr",
    "no sponsor", "doesn't require", "does not require",
    "right to work", "settled status", "presettled status",
)


def _requires_sponsorship(user_work_auth: Optional[dict], country: str) -> bool:
    """Return True if the user requires sponsorship for the given country code.

    Allow-list approach: any value containing one of the AUTHORIZED tokens
    means no sponsorship needed. Anything else (including 'requires_visa',
    'requires_sponsorship', 'visa needed', empty string) → True.
    """
    if not user_work_auth or not isinstance(user_work_auth, dict):
        return False
    val = user_work_auth.get(country) or user_work_auth.get(country.lower())
    if not val:
        return False
    val_lo = str(val).lower()
    if any(tok in val_lo for tok in _AUTHORIZED_TOKENS):
        return False
    return True


def apply_geo_score_cap(
    score_result: dict,
    job: dict,
    user_work_auth: Optional[dict],
    home_country: str = DEFAULT_HOME_COUNTRY,
) -> dict:
    """Apply geography- and work-auth-aware caps to score_result.

    Two-tier capping for a candidate based in `home_country`:
    1. Job in a non-home country → cap at NON_HOME_COUNTRY_CAP (A-tier max).
       Even if the employer sponsors, the candidate prefers home-country jobs.
    2. Additionally, if the user requires sponsorship for the job's country
       AND the JD shows no sponsor signal → cap at SPONSOR_REQUIRED_CAP
       (B-tier max). Soft sponsor signals → SPONSOR_SOFT_CAP.

    Mutates and returns the score dict. Annotates gaps[] for transparency.
    Pure-data-driven: no AI calls, no DB queries, deterministic.
    """
    if not score_result or not isinstance(score_result, dict):
        return score_result

    country = _detect_country(job.get("location"))
    if not country:
        return score_result  # ambiguous location → preserve recall
    if country == home_country:
        return score_result  # home-country job, no cap

    description = job.get("description") or ""
    gaps = score_result.setdefault("gaps", [])

    # Tier 1: non-home-country always caps at A-tier max
    cap = NON_HOME_COUNTRY_CAP
    gap_marker = f"job_outside_{home_country.lower()}"

    # Tier 2: stricter cap if user requires sponsorship AND JD doesn't sponsor
    if _requires_sponsorship(user_work_auth, country):
        if not _has_hard_sponsor_signal(description):
            cap = (SPONSOR_SOFT_CAP if _has_soft_sponsor_signal(description)
                   else SPONSOR_REQUIRED_CAP)
            gap_marker = f"requires_{country.lower()}_visa_sponsorship"

    # Apply cap to all four scores
    for key in ("match_score", "ats_score", "hiring_manager_score", "tech_recruiter_score"):
        if key in score_result and isinstance(score_result[key], (int, float)):
            score_result[key] = min(score_result[key], cap)

    if isinstance(gaps, list) and gap_marker not in gaps:
        gaps.append(gap_marker)

    return score_result


# Backwards-compatible alias for the older name (in case other code references it
# before this module is widely adopted).
apply_work_auth_cap = apply_geo_score_cap
