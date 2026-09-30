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


# Country aliases -> the internal codes _detect_country emits. Needed because
# nothing normalised the two ends of this lookup against each other: the
# onboarding form (web/src/pages/Onboarding.jsx, WorkAuthRow) takes the country
# as FREE TEXT, so `users.work_authorizations` is keyed by country NAMES, while
# _detect_country returns codes. Read live on 2026-09-30:
#
#   {"India": "citizen", "Germany": "requires_sponsorship",
#    "Ireland": "stamp_1g", "United States": "requires_sponsorship",
#    "United Kingdom": "requires_sponsorship"}
#
# `get("US")` on that dict is None, so _requires_sponsorship returned False for
# every country and SPONSOR_REQUIRED_CAP had never fired in production. A US
# role needing sponsorship capped at NON_HOME_COUNTRY_CAP (89, A-tier) instead
# of 70 (B-tier).
#
# Codes are the ones _detect_country already uses and are NOT strictly ISO
# 3166-1: the United Kingdom is "UK" here, not "GB". Keeping them aligned with
# _detect_country matters more than being standards-correct, because these two
# functions only ever talk to each other. Real ISO codes are accepted as
# aliases anyway, so data written either way resolves.
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "US": ("us", "usa", "u.s.", "u.s.a", "united states",
           "united states of america", "america"),
    "UK": ("uk", "gb", "gbr", "united kingdom", "great britain", "britain",
           "england", "scotland", "wales", "northern ireland"),
    "IE": ("ie", "irl", "ireland", "republic of ireland", "eire"),
    "CA": ("ca", "can", "canada"),
    "DE": ("de", "deu", "ger", "germany", "deutschland"),
    "FR": ("fr", "fra", "france"),
    "NL": ("nl", "nld", "netherlands", "the netherlands", "holland"),
    "SG": ("sg", "sgp", "singapore"),
    "AU": ("au", "aus", "australia"),
    "IN": ("in", "ind", "india"),
}

_ALIAS_TO_CODE: dict[str, str] = {
    alias: code for code, aliases in _COUNTRY_ALIASES.items() for alias in aliases
}


def _normalize_key(raw: object) -> Optional[str]:
    """A country name, code or alias -> the internal code, or None.

    Exact match after normalisation, never substring: "in" is a legitimate
    alias for India as a whole key, and a disaster as a substring.
    """
    if not raw:
        return None
    flat = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    flat = " ".join(flat.split())
    if flat in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[flat]
    # "u.s." and "u.s.a" carry dots as aliases; strip them only as a fallback so
    # a dotted alias still wins on its own terms.
    return _ALIAS_TO_CODE.get(flat.replace(".", ""))


def _auth_value_for(user_work_auth: dict, country: str) -> Optional[str]:
    """The user's status for `country`, whatever shape the key was written in."""
    target = _normalize_key(country) or str(country).strip().upper()
    for key, val in user_work_auth.items():
        if (_normalize_key(key) or str(key).strip().upper()) == target:
            return val
    return None


def _requires_sponsorship(user_work_auth: Optional[dict], country: str) -> bool:
    """Return True if the user requires sponsorship for the given country code.

    Allow-list approach: any value containing one of the AUTHORIZED tokens
    means no sponsorship needed. Anything else (including 'requires_visa',
    'requires_sponsorship', 'visa needed') → True. An ABSENT country still
    returns False, deliberately: silence about a country is not evidence the
    candidate needs sponsorship there, and guessing would cap on no data.

    Both ends of the comparison are normalised, and both halves matter:

    * the KEY, because the stored dict is keyed by country names while
      `country` is a code (see _COUNTRY_ALIASES);
    * the VALUE, because the same form writes underscored statuses --
      `permanent_resident`, `stamp_1g`, `stamp_4` -- and _AUTHORIZED_TOKENS
      spells them with spaces. Three of the form's six options are AUTHORIZED
      statuses that the token list could not recognise.

    Fixing only the key would have been worse than leaving the bug: a US
    permanent resident would go from under-capped at 89 to capped at 70 as
    though they needed sponsorship, which hides good jobs instead of merely
    over-promoting bad ones.
    """
    if not user_work_auth or not isinstance(user_work_auth, dict):
        return False
    val = _auth_value_for(user_work_auth, country)
    if not val:
        return False
    # Separators normalised so the form's `stamp_1g` matches the list's
    # `stamp 1g`. Done here rather than by adding underscore spellings to
    # _AUTHORIZED_TOKENS so the list stays a list of phrases, not of encodings.
    val_lo = str(val).lower().replace("_", " ").replace("-", " ")
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
