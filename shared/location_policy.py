"""One user's geographic preference, resolved once and used by every scraper.

Until 2026-09-28 each board scraper carried its own private copy of

    LOCATION_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe",
                         "anywhere"}

(`lambdas/scrapers/scrape_greenhouse.py:30` and `scrape_ashby.py:30`,
byte-identical), and the search-URL scrapers hardcoded `"location": "Ireland"`
in template.yaml. The owner's configured `locations` -- edited in Settings,
stored in `user_search_configs.locations` -- reached `load_config.py` and was
used for exactly one thing: salting the `query_hash` cache key. It never
reached a scraper, so changing it in the UI changed nothing about what got
scraped.

This module is the single place that answers "does this posting's location
match what the user asked for", for whoever asks. It is the geographic
counterpart of `merge_dedup.PrefilterProfile` and follows the same shape
deliberately:

  * a frozen dataclass resolved ONCE per run from the user's own config,
  * tiered resolution that reports WHICH tier supplied the answer, so a
    silent fallthrough shows up in the logs instead of being inferred,
  * exactly one hardcoded single-tenant default, scoped to the case where
    the profile expresses no location at all, with its removal condition
    written down.

It lives in `shared/` rather than next to either caller because the region
reference data below is needed on both sides of the pipeline: by the
scrapers, which filter at fetch time, and by `merge_dedup`'s Rule 4, which
filters again at merge time. Two copies of "Bangalore is in India" is how
this module's own predecessor went wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Reference data. Facts about geography, true for every user, as distinct
# from policy, which is per-user and derived from their config below.
# ---------------------------------------------------------------------------

# Region name -> location tokens that identify it. Needed because scrapers
# frequently emit a bare city with no country ("Dublin", "Bangalore"), so
# matching on the region name alone would fail open on exactly the rows a
# geographic rule is written to catch. Extend as users configure new regions.
#
# Moved here from merge_dedup.py (which still re-exports the name) so the
# scrape-time filter and merge_dedup's Rule 4 cannot drift apart about which
# city belongs to which region.
REGION_LOCATION_TOKENS = {
    "ireland": frozenset({"ireland", "dublin", "cork", "galway", "limerick"}),
    "india": frozenset({
        "india", "bangalore", "bengaluru", "mumbai", "hyderabad", "pune",
        "chennai", "delhi", "gurgaon", "noida", "kolkata",
    }),
    "us": frozenset({
        "united states", "usa", "u.s.", " us ", "austin", "new york", "seattle",
        "san francisco", "boston", "chicago", "denver", "atlanta",
    }),
    "uk": frozenset({
        "united kingdom", "england", "london", "manchester", "birmingham",
        "edinburgh", "glasgow", "bristol",
    }),
}

# Supranational labels a posting may use INSTEAD of naming a country, mapped
# to the regions they contain. "Remote - EMEA" is a real posting a
# Dublin-based candidate can take; "EMEA" is not a word the user typed, so it
# is admitted only because Ireland is inside it. Derived the same way for
# everyone: a Mumbai-based user gets APAC, not Europe.
REGION_CONTAINERS = {
    "ireland": frozenset({"europe", "emea", "eu", "european union"}),
    "uk": frozenset({"europe", "emea", "eu"}),
    "us": frozenset({"north america", "americas", "namer"}),
    "india": frozenset({"apac", "asia", "asia pacific"}),
}

# Words a posting uses to say "this role is not tied to a place".
#
# Evidence-first, measured against the live jobs_raw corpus (13,677 rows,
# 2026-09-28) -- the same discipline merge_dedup's title vocabularies follow.
# Occurrence counts in that corpus: remote 6,071, global 19, worldwide 17,
# anywhere 5.
#
# "distributed" is deliberately NOT here despite being a plausible synonym. It
# appears twice in the corpus (both "Distributed EMEA", which admits on the
# EMEA container anyway), but a live fetch of the configured Greenhouse boards
# on 2026-09-28 found 56 Cloudflare postings whose location is the bare string
# "Distributed" and whose TITLES are explicitly place-bound -- "Senior Customer
# Engineer, Majors - Detroit, MI", "Senior Named Account Executive (Dallas)".
# At Cloudflare the word means "we have no office", not "anyone anywhere".
# Admitting it would have added 56 US-bound postings and nothing else.
# "virtual", "wfh", "telecommute" and "work from home" have ~zero corpus
# support and are left out for the same reason.
ANYWHERE_MARKERS = ("anywhere", "worldwide", "remote", "global")

# Words that may survive marker-stripping without meaning a place. Kept
# deliberately tiny: everything else the corpus leaves behind after stripping
# the markers above is a real country, state or city ("us" 2,258, "usa" 2,077,
# "canada" 576, "india" 359, "colombia" 108 ...), and treating any of those as
# noise is what would let a US-only remote role back in.
#
# Note what is NOT here: the single letters "u", "s", "c" and "d", which come
# from "U.S." and "D.C." and are place fragments, and "hybrid"/"onsite", which
# say something real about the arrangement.
RESIDUE_FILLER = frozenset({
    "a", "an", "and", "at", "from", "hiring", "home", "in", "of", "only",
    "or", "the", "to", "work",
})

# Last-resort geographic default, for a profile that expresses NO location.
#
# This is a single-tenant default and therefore exactly the kind of thing this
# module exists to stop scrapers from carrying. It is here, and not simply
# "no gate at all", for the same reason merge_dedup keeps
# LEGACY_REMOTE_ONLY_REGIONS: a scraper with no location gate stores every
# posting on every configured board. Measured on the live boards
# (2026-09-28): Greenhouse alone returns 3,089 postings per run against the
# 622 today's filter admits, and jobs_raw already holds 13,677 rows.
#
# Scoped as tightly as it can be. It is a DEFAULT LOCATION LIST fed through
# the identical code path, not a second matching algorithm and not an
# override: `build_location_policy` consults it only when the caller supplies
# no usable location at all, so a user who has expressed any preference --
# including a Mumbai-based user whose local market the old hardcoded set
# rejected outright -- never sees it. `LocationPolicy.source` reports which
# tier applied and every caller logs it, so this cannot go stale unnoticed.
#
# REMOVAL: give the profile any location and this never fires again. Delete
# the constant outright once `locations` is required at signup rather than
# defaulted -- today `load_config` supplies ["ireland"] for a user with no
# search-config row, so the only way to reach this tier is to clear the field.
LEGACY_DEFAULT_LOCATIONS = ("Ireland",)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocationPolicy:
    """One user's geographic preference, resolved from their own config.

    accept_tokens   lowercase substrings that admit a posting: the user's own
                    location strings, every city/country token of the regions
                    those resolve to, and those regions' supranational
                    containers.
    target_regions  the REGION_LOCATION_TOKENS keys the user is targeting.
    search_term     the single location string to hand a job board whose
                    search URL takes one (LinkedIn, Indeed, Glassdoor).
    locations       the user's configured strings, verbatim, for logging.
    source          which tier supplied the policy: "user_config" or
                    "legacy_fallback". Log it.
    """

    accept_tokens: frozenset = frozenset()
    target_regions: frozenset = frozenset()
    search_term: str = ""
    locations: tuple = ()
    source: str = "none"


def normalize_locations(raw) -> list:
    """Flatten whatever `locations` holds into a flat list of strings.

    Tolerates every shape the project uses: a flat list (what Settings.jsx
    and the /api/search-config endpoint write), config.yaml's
    {"primary": [...], "secondary": [...]} nesting, a bare string, and None.
    Shape-tolerant on purpose -- assuming one shape for a jsonb column is
    what left merge_dedup's Rule 4 silently disabled in production.

    merge_dedup._config_locations does the same job for the same column and
    predates this; it is left where it is rather than re-pointed, so that a
    change here cannot move Rule 4 as a side effect.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        out = []
        for value in raw.values():
            if isinstance(value, list):
                out.extend(str(x) for x in value if str(x).strip())
            elif value:
                out.append(str(value))
        return [s for s in (x.strip() for x in out) if s]
    if isinstance(raw, (list, tuple, set)):
        return [s for s in (str(x).strip() for x in raw) if s]
    text = str(raw).strip()
    return [text] if text else []


def _normalized(text: str) -> str:
    """Lowercase, with every separator collapsed to a single space.

    So "Cork, Munster, IE, T12 H682", "US-Remote" and "Remote (US)" all
    reduce to space-separated words and can be matched the same way.
    """
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _contains_token(normalized_haystack: str, token: str) -> bool:
    """Is `token` present in an already-normalized string, as whole words?

    Word boundaries, not substring. Substring matching on short tokens is a
    silent, hard-to-spot bug and this module found one in itself: the "eu"
    container admitted "Landeshauptstadt Munchen, DEU" -- a Munich office --
    because "eu" sits inside "DEU". It is the same class of collision
    merge_dedup documents for "cto" inside "director" and works around for
    "us" by padding the token to " us " (which in turn silently misses
    "US-Remote", because the separator is a hyphen and not a space).
    Normalising the haystack and matching on boundaries fixes both.
    """
    words = _normalized(token).split()
    if not words:
        return False
    pattern = r"\s+".join(re.escape(w) for w in words)
    return re.search(r"\b" + pattern + r"\b", normalized_haystack) is not None


def regions_named(location: str) -> frozenset:
    """Every configured region whose tokens appear in `location`.

    Plural, unlike merge_dedup._region_for_location, which returns the first
    match. A posting location is routinely a list ("Remote, Canada; Remote,
    US"), and "does this name anywhere the user did not ask for" needs all of
    them, not whichever the dict happened to yield first.
    """
    haystack = _normalized(location)
    return frozenset(
        region for region, tokens in REGION_LOCATION_TOKENS.items()
        if any(_contains_token(haystack, token) for token in tokens)
    )


def _preferred_search_term(locations: list) -> str:
    """The one location string to give a board whose search takes only one.

    Prefers a configured location that IS a region name over one inside it,
    because these boards' location filters are hierarchical: LinkedIn's
    `location=Ireland` is a superset of `location=Dublin`, so picking the
    country-level term cannot return less than picking the city.

    For the owner's ['Dublin', 'Ireland'] this yields "Ireland", which is
    byte-identical to the value template.yaml hardcoded before this change --
    so threading the config through these scrapers cannot narrow their
    results. For ['Mumbai'] it yields "Mumbai".

    It is a heuristic, and it is here rather than a fan-out over every
    configured location on purpose: searching N locations multiplies both the
    Bright Data request spend and the raw-posting count, and for a nested pair
    like Dublin/Ireland almost every extra result is a duplicate -- the
    opposite of the "fewer raw postings" this change exists to deliver.
    """
    for loc in locations:
        if loc.strip().lower() in REGION_LOCATION_TOKENS:
            return loc.strip()
    return locations[0].strip() if locations else ""


def build_location_policy(locations=None) -> LocationPolicy:
    """Resolve one user's `locations` config into a reusable policy.

    Two tiers, and the second one is reported rather than silent:

      1. the user's own locations, however they were shaped;
      2. LEGACY_DEFAULT_LOCATIONS, only when tier 1 is empty.

    A location the user typed that matches no known region -- "Berlin",
    "Remote", "Lisbon" -- still works: its literal text becomes an accept
    token. It simply gets no city/container expansion, because there is no
    reference data to expand it with. Failing that way round is deliberate:
    an unrecognised preference admits less than a recognised one, never more,
    and never silently admits somewhere the user did not ask for.
    """
    resolved = normalize_locations(locations)
    source = "user_config"
    if not resolved:
        resolved = list(LEGACY_DEFAULT_LOCATIONS)
        source = "legacy_fallback"

    tokens: set = set()
    regions: set = set()
    for loc in resolved:
        lowered = loc.lower()
        tokens.add(lowered)
        for region in regions_named(lowered):
            regions.add(region)
            # .strip() drops the padding REGION_LOCATION_TOKENS puts around
            # " us "; _contains_token matches on word boundaries, so the
            # padding is both unnecessary and (being a literal space) wrong.
            tokens |= {t.strip() for t in REGION_LOCATION_TOKENS[region]}
            tokens |= REGION_CONTAINERS.get(region, frozenset())

    return LocationPolicy(
        accept_tokens=frozenset(t for t in tokens if t),
        target_regions=frozenset(regions),
        search_term=_preferred_search_term(resolved),
        locations=tuple(resolved),
        source=source,
    )


_MARKER_PATTERN = re.compile(
    r"\b(" + "|".join(ANYWHERE_MARKERS) + r")\w*\b"
)


def _has_anywhere_marker(normalized_location: str) -> bool:
    return bool(_MARKER_PATTERN.search(normalized_location))


def _location_independent_residue(normalized_location: str) -> str:
    """What is left of an already-normalized location once the "not tied to a
    place" words go.

    "remote" -> "", "remote us" -> "us", "remote worldwide" -> "",
    "hiring globally" -> "" (the trailing "\\w*" eats the "-ly").
    An empty residue is the test for genuine location independence.
    """
    text = _MARKER_PATTERN.sub(" ", normalized_location)
    words = [w for w in text.split() if w not in RESIDUE_FILLER]
    return " ".join(words)


def location_verdict(
    location: str,
    policy: LocationPolicy,
    is_remote: bool = False,
) -> tuple[bool, str]:
    """Should this posting be scraped? Returns (admit, reason).

    Returns a reason rather than a bare bool so a scraper can log WHY a board
    collapsed from 300 postings to 4, which the previous
    `any(kw in loc for kw in LOCATION_KEYWORDS)` could not.

    Four checks, in order:

    0. No location at all -> admit. Fails open, consistent with
       merge_dedup's Rule 0 (a missing posted_date does not reject) and
       _region_for_location (an unrecognised location does not reject): an
       input we know nothing about is not evidence of irrelevance.

    1. Names somewhere the user asked for -> admit. Substring, so a
       multi-location posting admits when ANY part matches ("Dublin,
       Ireland; London, England" admits on Dublin), and so "Remote -
       Ireland" admits here rather than falling through to the remote logic.

    2. Location-independent -> admit. THE deliberate decision in this module:
       "remote" is not a place the user typed, so it admits only when the
       posting names no other place. "Remote" and "Remote (Worldwide)" are
       open to a Dublin-based candidate; "Remote - US", "Remote, India" and
       "Remote, Canada; Remote, US" are country-scoped roles that merely
       happen to be work-from-home, and reading the word "remote" in them as
       a universal admit is precisely what made the old filter admit 100% of
       every Greenhouse board (6,895 of jobs_raw's 13,677 rows).

       Ashby's per-posting `isRemote` flag feeds in here rather than
       short-circuiting the whole check, which is what it used to do: it made
       983 "New York, NY (HQ)" postings unconditionally admissible.

    3. Otherwise -> reject. It names a place, and not one of theirs.
    """
    haystack = _normalized(location)
    if not haystack and not is_remote:
        return True, "no_location"

    for token in sorted(policy.accept_tokens, key=len, reverse=True):
        if _contains_token(haystack, token):
            return True, f"target:{token}"

    if is_remote or _has_anywhere_marker(haystack):
        residue = _location_independent_residue(haystack)
        if not residue:
            return True, "location_independent"
        return False, f"remote_elsewhere:{residue[:40]}"

    return False, "outside_target"


def location_matches(
    location: str,
    policy: LocationPolicy,
    is_remote: bool = False,
) -> bool:
    """Boolean form of location_verdict, for use in a list comprehension."""
    return location_verdict(location, policy, is_remote)[0]
