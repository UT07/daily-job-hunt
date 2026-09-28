"""Merge and deduplicate scraped jobs, apply relevance pre-filter.

Reads from jobs_raw (shared pool) and scrape_runs (output contract).
Applies 3-tier dedup (exact hash + exact company+title + fuzzy) and
relevance pre-filter (incl. freshness) before passing job hashes to
score_batch.
"""
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lazy SSM client — boto3.client at module load forces AWS_DEFAULT_REGION
# on every importer (including unit tests + runtime-import smoke). Same
# pattern as ai_helper.py shipped in PR #23.
_ssm = None


def _get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm

# ---------------------------------------------------------------------------
# Rule 1 title vocabularies. Split by SOURCE OF TRUTH, not by matching style.
#
# Until 2026-09-28 this was a single flat REJECT_TITLE_KEYWORDS set applied to
# every user. It conflated three unrelated policies with three different
# correct owners, which is why PR #100 (a798e4c) could not fix any of them
# incrementally and deferred the whole set as "too risky":
#
#   1. "not a job posting at all"  -> universal fact, belongs in code (below)
#   2. "too senior / wrong track"  -> relative to the user's own target level,
#                                     derived from experience_levels
#   3. "wrong function for me"     -> depends entirely on the user's field,
#                                     derived from their own queries
#
# The consequence of conflating them was that a non-IT user had their OWN
# target role rejected before Rule 3 (the domain-neutral one PR #100 added)
# ever ran: "Financial Controller" -> role_mismatch:controller, "Marketing
# Manager" -> role_mismatch:manager, "Project Architect" ->
# role_mismatch:architect, "Data Scientist" -> role_mismatch:data scientist.
#
# Two principles now govern every derived vocabulary:
#
#   A. A term the user searched for cannot reject them. "Manager" is a
#      people-management track in tech and an IC seniority label in
#      marketing, finance and product ("Marketing Manager", "Brand Manager").
#      The word alone cannot tell you which, but the user's own query can.
#   B. The IT vocabularies load only for a user whose queries are IT-shaped.
#      A nurse doesn't get them overridden; they are never loaded at all.
# ---------------------------------------------------------------------------


def _compile_title_pattern(keywords) -> re.Pattern:
    """Word-boundary alternation over `keywords`, longest-first.

    Word boundaries throughout, deliberately. The old set matched most
    keywords as bare substrings, which silently swallowed unrelated titles --
    "cto" is inside "direCTOr", "doCTOr", "inspeCTOr", "colleCTOr",
    "contraCTOr", "refraCTOry" and "seCTOr". That was invisible in an
    all-tech corpus (the only colliding title such a corpus contains,
    "...Director", is independently rejected by the "director" keyword) and so
    survived the 2026-09-25 corpus tuning, which did hunt this exact class of
    bug for "sales" vs "Salesforce" and "partner" vs "Partnerships".
    Word-boundary matching is strictly more conservative than substring
    matching -- it can only ever admit more, never reject more.

    A trailing "s?" allows the plural ("Recruiters", "Data Scientists") that
    the old substring match caught for free. On a multi-word keyword it
    attaches to the final word, and on one like "head of" it is simply
    inert.
    """
    alternation = "|".join(
        re.escape(k) + "s?" for k in sorted(keywords, key=len, reverse=True)
    )
    return re.compile(r"\b(" + alternation + r")\b", re.IGNORECASE)


# (1) Not a job posting at all. Universal -- true for a nurse, an accountant
# and an SRE alike -- so this is the one title vocabulary that stays
# hardcoded and is NOT overridable by the user's queries.
#
# Deliberately minimal. "summit" is here because it was observed verbatim on
# a real Greenhouse board ("2026 - Women in Tech Summit, EMEA"). Other
# plausible members ("career fair", "hiring event", "webinar") are NOT added
# without a corpus hit first -- that evidence-before-keyword discipline is
# what the 2026-09-25 tuning established and it is worth keeping.
STRUCTURAL_REJECT_KEYWORDS = frozenset({"summit"})
_STRUCTURAL_REJECT_PATTERN = _compile_title_pattern(STRUCTURAL_REJECT_KEYWORDS)

# (2) Seniority / track tiers. Which tiers are excluded is NOT hardcoded --
# it derives from the user's experience_levels via _LEVEL_TIER_EXCLUSIONS.
SENIORITY_TIER_KEYWORDS = {
    "exec": frozenset({
        "director", "vp", "vice president", "head of", "chief", "cto", "cio",
        "distinguished",
    }),
    "management": frozenset({"manager"}),
}
_SENIORITY_TIER_PATTERNS = {
    tier: _compile_title_pattern(kws) for tier, kws in SENIORITY_TIER_KEYWORDS.items()
}

# experience_level -> the tiers that level rules out. Owner-specified
# 2026-09-28. "senior" deliberately still sees management-track postings: a
# senior IC can judge an EM req for themselves, and over-filtering is the
# failure mode this pipeline actually suffers from (a near-empty dashboard),
# not under-filtering. "lead"/"manager" rule out nothing.
#
# When several levels are selected the MOST PERMISSIVE wins (set
# intersection) -- otherwise ticking "senior" next to "mid_level" in Settings
# would be silently ignored, which is a confusing thing for a UI to do.
_LEVEL_TIER_EXCLUSIONS = {
    "entry_level": frozenset({"exec", "management"}),
    "mid_level": frozenset({"exec", "management"}),
    "senior": frozenset({"exec"}),
    "lead": frozenset(),
    "manager": frozenset(),
}

# Internships are their own axis, NOT a seniority tier. Deriving "no
# internships" from entry_level would be wrong in both directions: a new
# graduate legitimately wants them, and a career-changing senior might too.
# Default off, matching the owner's preference (feedback_graduated.md).
#
# FOLLOW-UP: the owner's real rule is narrower than "no internships" -- they
# want to exclude only internships that REQUIRE CURRENT ENROLMENT, which is a
# condition on the JD body, not the title. Deliberately out of scope here;
# this flag is the title-level approximation.
INTERNSHIP_KEYWORDS = frozenset({"intern", "internship"})
_INTERNSHIP_PATTERN = _compile_title_pattern(INTERNSHIP_KEYWORDS)

# (3) Off-function titles for an IT candidate. Loaded ONLY when the user's own
# queries are IT-shaped (see _looks_like_tech_domain), and individually
# cancelled by any query the user actually wrote. So "controller" still
# rejects a Financial Controller req for an SRE, but never for the finance
# user who searched for it -- and it is not even loaded for a nurse.
#
# These are the terms from the pre-2026-09-28 flat set that encode "this is
# not hands-on IC engineering": sales/GTM/pre-sales, plus tracks with no
# resume archetype to tailor against. "sales" and "partner" are still
# deliberately absent as bare keywords (the Salesforce / Security
# Partnerships collisions from the 2026-09-25 tuning).
IT_OFF_FUNCTION_KEYWORDS = frozenset({
    "sales engineer", "sales specialist", "sales operations", "account executive",
    "business development", "solutions consultant", "presales", "pre-sales",
    "professional services", "developer advocate", "data scientist", "recruiter",
    "compensation analyst", "controller", "revenue analytics",
    "revenue technology", "revenue technical", "architect",
})
_IT_OFF_FUNCTION_PATTERN = _compile_title_pattern(IT_OFF_FUNCTION_KEYWORDS)

# ---------------------------------------------------------------------------
# Rule 4 geography. The same fact/policy split as Rule 1.
#
# The old rule hardcoded {"india", "bangalore", "mumbai", ...} and rejected
# in-office roles there -- the Dublin-based owner's personal exclusion,
# applied to every user, which rejected a Mumbai-based user's entire local
# market. The city list survives below as REFERENCE DATA ("Bangalore is in
# India" is true for everyone). The POLICY -- which regions are acceptable
# only remotely -- now reads the `remote_only` flag that the user's own
# geo_regions config has always carried and that this filter simply ignored.
# ---------------------------------------------------------------------------

# Region name -> location tokens that identify it. Needed because scrapers
# frequently emit a bare city with no country ("Dublin", "Bangalore"), so
# matching on the region name alone would fail open on exactly the rows the
# old rule was written to catch. Extend as users configure new regions.
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

# Regions whose remote-only status Rule 4 does NOT yet enforce.
#
# BEHAVIOUR-PRESERVATION CARVE-OUT (owner decision, 2026-09-28), sized by
# measurement rather than by guesswork, after two live corpus runs over the
# same 13,677-row pool:
#
#   - Deriving the policy from geo_regions[].remote_only alone DISABLED Rule 4
#     in production (that key is written nowhere but config.yaml, so the DB row
#     never carries it): incompatible_location 6 -> 0, and five Hyderabad/
#     Bangalore in-office roles admitted against explicit owner intent.
#   - Deriving it from `locations` with only "us" exempt OVER-rejected: the
#     owner's locations are ['Dublin','Ireland'], so {india,uk,us} all became
#     remote-only and 107 London/Manchester/Edinburgh SRE and DevOps roles --
#     the owner's exact target roles -- were newly rejected (-0.75pp).
#
# The second failure is the instructive one: `locations` states a PREFERENCE,
# not an exclusion. The pre-2026-09-28 rule excluded exactly one region and
# left every other foreign location to shared/work_auth.py, which CAPS a
# non-home-country job to A-tier instead of rejecting it. Turning a preference
# list into an exclusion set silently contradicts that shipped design.
#
# So the exemption names the regions the old rule tolerated in-office. Tier 2
# stays genuinely per-user -- a Mumbai-based user gets the mirror-image policy
# from the same code path, with no special-casing -- while the owner's admit
# rate is unchanged.
#
# Expressed as an EXEMPTION rather than an allow-list on purpose. Scoping
# enforcement to named regions instead ("enforce india only") preserved the
# owner's behaviour but silently disabled Rule 4 for everyone else, which
# re-introduces the single-tenant assumption this change exists to remove.
#
# RETIRING IT: clear the env var. Correct, and costs this user ~107 UK jobs,
# so it wants its own measured decision -- see
# test_retiring_the_exemption_enforces_uk_too, already written.
GEO_REMOTE_ONLY_EXEMPT = os.environ.get("GEO_REMOTE_ONLY_EXEMPT", "us,uk")

# Last-resort geographic policy, for a profile where NOTHING expresses one.
#
# This is a single-tenant default and therefore exactly the kind of thing this
# module is supposed to have stopped carrying. It is here because the live
# corpus run (13,677 rows, 2026-09-28) proved that deriving the policy from
# geo_regions[].remote_only alone disables Rule 4 in production: that key is
# written nowhere but config.yaml -- not by the search-config API, not by
# Settings.jsx -- so the DB row never has it. The measured effect was
# incompatible_location rejects 6 -> 0 and five Hyderabad/Bangalore in-office
# roles admitted against explicit owner intent.
#
# Scoped as tightly as it can be: it fires ONLY when neither an explicit
# remote_only flag nor any target `locations` is available, so any user who has
# expressed a geographic preference at all is unaffected -- including the
# Mumbai-based user whose local market the old hardcoded list used to reject.
#
# REMOVAL: populate either signal for the user and this never fires again.
# PrefilterProfile.geo_policy_source reports which tier actually applied, and
# the handler logs it on every run so this cannot go stale unnoticed.
LEGACY_REMOTE_ONLY_REGIONS = frozenset({"india"})

# Freshness pre-filter — drop jobs whose posted_date is older than this many
# days at scrape time. Configurable via JOB_MAX_AGE_DAYS env var. Default 14:
# strikes a balance between "first-mover advantage" (the user's goal: apply
# ASAP) and tolerating sources that update posted_date only weekly. Jobs
# where posted_date is None pass through this filter unchanged — we don't
# want to throw away rows just because the source didn't supply it.
JOB_MAX_AGE_DAYS = int(os.environ.get("JOB_MAX_AGE_DAYS", "14"))

# How many days back Source 3 (backfill, see handler() below) looks in
# jobs_raw for rows that were scraped but never made it into the scored
# `jobs` table. Was a hardcoded 7 — anything that missed that window was
# lost permanently, not merely delayed (2026-09-25 audit, P0-4): with
# scoring throughput capped well below scrape volume, a job can easily sit
# unscored for longer than a week without anything being wrong.
#
# Widened, not made unbounded. A job's own staleness is already governed
# independently by Rule 0 above (JOB_MAX_AGE_DAYS, keyed off posted_date,
# not scraped_at) — widening how far back we SEARCH for unscored rows does
# not let a genuinely stale posting reach the user, because Rule 0 still
# rejects it either way. What an unbounded window WOULD cost is a
# jobs_raw scan that grows forever (12,672 rows today, more every day this
# pipeline runs), re-fetched on every single invocation. 30 days is
# double JOB_MAX_AGE_DAYS's default and comfortably covers any realistic
# pipeline gap this project has actually had (the current parking is a
# deliberate months-long exception, not something a backfill window should
# be sized around — a gap that long calls for fresh scraping, not
# resurrecting month-old raw rows whose postings are almost certainly gone).
BACKFILL_LOOKBACK_DAYS = int(os.environ.get("BACKFILL_LOOKBACK_DAYS", "30"))

# Hard ceiling on how many jobs one run hands to ScoreBatchMap, regardless of
# how many pass the relevance filter below. Groq's free tier caps AI scoring
# at roughly 80-120 jobs/day (8,000 tokens/minute ≈ 1.4 calls/minute); 150
# leaves headroom without depending entirely on the content filter to hold
# the line on a burst day. On 2026-09-01 (1,427 raw jobs, the single
# biggest day on record) the tuned relevance filter alone still admitted
# more than this on its own — see the throughput fix report. When more than
# MAX_JOBS_PER_RUN pass the relevance filter, keep the best ones (highest
# tech-skill overlap with the user's stack) rather than an arbitrary/
# order-dependent subset — see _job_relevance_rank below.
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "150"))

# Tech skill vocabulary, used for two things and NO LONGER as an
# unconditional base for every user's skill set.
#
# Before 2026-09-28 `user_skills` started as this set for everybody and the
# user's own query words were unioned on top, so a nurse's skill set
# contained "kubernetes". That was near-harmless for Rule 3 (a nursing JD
# rarely contains two of these) but it quietly broke _job_relevance_rank,
# which ranks by exactly this overlap: every non-IT JD scored 0, so when a
# run exceeded MAX_JOBS_PER_RUN the surviving 150 were chosen arbitrarily.
# That is the worse of the two failures because it is silent -- no reject
# reason, no log line, the job just loses a tiebreak.
#
# Now it serves as (a) the probe for whether a user's queries are IT-shaped
# and (b) the skill vocabulary for those users only. Name retained because
# scripts/tune_prefilter.py and the existing test corpus both reference it.
DEFAULT_USER_SKILLS = {
    "python", "aws", "kubernetes", "docker", "terraform", "react", "typescript",
    "node", "fastapi", "linux", "ci/cd", "devops", "sre", "cloud", "java",
    "javascript", "golang", "go", "microservices", "api",
}


def get_param(name):
    return _get_ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _normalize_title(title: str) -> str:
    """Strip seniority prefixes for fuzzy matching."""
    title = title.lower().strip()
    for prefix in ("senior ", "junior ", "lead ", "staff ", "principal ", "sr. ", "jr. "):
        title = title.replace(prefix, "")
    # Strip Roman numeral suffixes
    title = re.sub(r'\s+(i{1,3}|iv|v)\s*$', '', title)
    return title.strip()


def _fuzzy_match(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are similar enough."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


def _extract_tech_keywords(text: str) -> set:
    """Extract tech keywords from job description using regex."""
    text = text.lower()
    found = set()
    tech_patterns = [
        "python", "java", "javascript", "typescript", "golang", "go", "rust", "c\\+\\+",
        "ruby", "php", "scala", "kotlin", "swift", "react", "angular", "vue",
        "node\\.?js", "django", "flask", "fastapi", "spring", "express",
        "aws", "azure", "gcp", "google cloud", "kubernetes", "k8s", "docker",
        "terraform", "ansible", "jenkins", "ci/cd", "github actions",
        "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "linux", "devops", "sre", "site reliability", "microservices", "api",
        "machine learning", "ml", "ai", "deep learning", "nlp",
    ]
    for pattern in tech_patterns:
        if re.search(r'\b' + pattern + r'\b', text):
            found.add(pattern.replace("\\", "").replace(".?", ""))
    return found


@dataclass(frozen=True)
class PrefilterProfile:
    """Every vocabulary Rules 1/3/4 consult, resolved for ONE user.

    Built once per run by build_prefilter_profile() from the user's own
    search config, then passed into _prefilter_job. The point of gathering
    these into one object is that it becomes impossible to add a new
    hardcoded list to the prefilter without deciding, explicitly and in one
    place, where that list comes from for a user who isn't the owner.

    skills             tech vocabulary + query tokens for an IT user; query
                       tokens ONLY otherwise
    query_phrases      bigrams from the user's queries (see _query_phrases)
    query_text         the user's queries joined, for cancellation lookups
    is_domain_tech     do the user's own queries look IT-shaped?
    excluded_tiers     seniority tiers ruled out by their experience_levels
    include_internships
    remote_only_regions region names the user accepts only remotely
    exempt_regions     regions whose remote_only flag is not yet enforced
                       (see GEO_REMOTE_ONLY_EXEMPT)
    geo_policy_source  which tier supplied the geographic policy, so a silent
                       fallback is visible in the logs rather than inferred
    """

    skills: frozenset = frozenset()
    query_phrases: frozenset = frozenset()
    query_text: str = ""
    is_domain_tech: bool = False
    excluded_tiers: frozenset = frozenset()
    include_internships: bool = False
    remote_only_regions: frozenset = frozenset()
    exempt_regions: frozenset = frozenset()
    geo_policy_source: str = "none"

    def cancels(self, keyword: str) -> bool:
        """Did the user ask for this term themselves?

        Principle A: a term the user searched for cannot reject them. This is
        what lets "manager" keep rejecting EM reqs for an SRE while admitting
        "Marketing Manager" for a marketer -- in marketing, finance and
        product "Manager" is an IC seniority label, not a people-management
        track, and no amount of keyword curation can distinguish those two
        senses. The user's own query can.
        """
        pattern = r"\s+".join(re.escape(w) for w in keyword.lower().split())
        return bool(re.search(r"\b" + pattern + r"\b", self.query_text))


def _looks_like_tech_domain(queries: list) -> bool:
    """Do the user's own search queries place them in software?

    Principle B: the IT vocabularies load only for IT users. This is the
    predicate that decides it, and it is deliberately based on the user's
    queries rather than on anything about the job being filtered -- the
    question "which vocabulary applies to this person" has to be answered
    per user, not per job, or a nurse's one AWS-mentioning JD would flip
    them into the IT branch.
    """
    return bool(_extract_tech_keywords(" ".join(queries)))


def _query_tokens(queries: list) -> frozenset:
    """Content words from the user's queries, for Rule 3's skill overlap.

    Same >2-char floor the pre-2026-09-28 handler used when it unioned query
    words onto DEFAULT_USER_SKILLS, so an IT user's Rule 3 behaviour is
    unchanged.
    """
    return frozenset(
        w for q in queries for w in re.findall(r"[A-Za-z]+", q.lower()) if len(w) > 2
    )


def _config_locations(raw) -> list:
    """Flatten the `locations` jsonb into a flat list of location strings.

    Tolerates both shapes the project uses: a flat list, and config.yaml's
    {"primary": [...], "secondary": [...]} nesting. Shape-tolerant on purpose
    -- assuming one shape for a jsonb column is what disabled Rule 4 in
    production in the first place.
    """
    if isinstance(raw, dict):
        out = []
        for v in raw.values():
            if isinstance(v, list):
                out.extend(str(x) for x in v)
            elif v:
                out.append(str(v))
        return out
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)] if raw else []


def _resolve_geo_policy(geo_regions, locations) -> tuple[frozenset, str]:
    """Which regions the user accepts only remotely, and where that came from.

    Three tiers, first one that actually expresses a policy wins. Returning
    the provenance alongside the value is deliberate: the bug this function
    exists to fix was a SILENT fallthrough to "no policy at all", which no
    amount of unit testing caught because the tests supplied a config shape
    production never produces. A caller that logs the source cannot be
    quietly wrong in the same way twice.

    Region names are resolved through the same matcher used on a job's
    location string, so "US (Remote)" in config and "Austin, Texas" on a
    posting cannot disagree about which region they mean.
    """
    regions = [r for r in (geo_regions or []) if isinstance(r, dict)]

    # Tier 1: an explicit per-region flag. Only counts if the key is actually
    # present -- a config that says remote_only=False everywhere IS a policy
    # ("in-office is fine anywhere") and must not fall through to tier 2.
    if any("remote_only" in r for r in regions):
        return (
            frozenset(filter(None, (
                _region_for_location(str(r.get("name", "")))
                for r in regions if r.get("remote_only")
            ))),
            "geo_regions.remote_only",
        )

    # Tier 2: the user's target locations. A region they are NOT targeting is
    # treated as acceptable only remotely -- derived this way it works for a
    # Mumbai-based user in reverse, without special-casing anybody.
    #
    # This is intentionally broader than the policy actually enforced, and
    # GEO_REMOTE_ONLY_EXEMPT narrows it. `locations` is a PREFERENCE list, so
    # reading it as a hard exclusion over-rejects: measured at 107 lost UK
    # SRE/DevOps jobs for the owner, whose locations are ['Dublin','Ireland'].
    # Non-home-country jobs are meant to be A-tier CAPPED by
    # shared/work_auth.py, not rejected here. Do not widen the enforced set
    # without re-running scripts/compare_prefilter_change.py.
    targets = frozenset(filter(None, (
        _region_for_location(str(loc)) for loc in (locations or [])
    )))
    if targets:
        return frozenset(REGION_LOCATION_TOKENS) - targets, "locations"

    # Tier 3: nothing to go on. See LEGACY_REMOTE_ONLY_REGIONS.
    return LEGACY_REMOTE_ONLY_REGIONS, "legacy_fallback"


def build_prefilter_profile(
    queries: list | None = None,
    experience_levels: list | None = None,
    geo_regions: list | None = None,
    locations: list | None = None,
    include_internships: bool = False,
    exempt_regions: str | None = None,
) -> PrefilterProfile:
    """Resolve one user's config into the vocabularies the prefilter needs.

    Every argument is optional and an absent one means "no gate", never "use
    the owner's value". That direction matters: PR #100 established that
    guessing a default for an unconfigured user is how a non-IT user ends up
    with an empty dashboard and a product that looks broken (its no-config
    `queries` fallback used to be ["software engineer"]). An unconfigured
    user here gets no seniority ceiling, no off-function list and no
    geographic rejection -- they see more, not less.
    """
    queries = list(queries or [])
    levels = [str(x).strip().lower() for x in (experience_levels or [])]

    is_tech = _looks_like_tech_domain(queries)
    tokens = _query_tokens(queries)

    # Most permissive selected level wins; an unrecognised level contributes
    # no exclusions rather than silently falling back to the strictest.
    if levels:
        excluded = frozenset.intersection(*[
            _LEVEL_TIER_EXCLUSIONS.get(lv, frozenset()) for lv in levels
        ])
    else:
        excluded = frozenset()

    remote_only, geo_source = _resolve_geo_policy(geo_regions, locations)

    return PrefilterProfile(
        skills=frozenset(DEFAULT_USER_SKILLS) | tokens if is_tech else tokens,
        query_phrases=_query_phrases(queries),
        query_text=" | ".join(q.lower() for q in queries),
        is_domain_tech=is_tech,
        excluded_tiers=excluded,
        include_internships=include_internships,
        remote_only_regions=remote_only,
        geo_policy_source=geo_source,
        exempt_regions=frozenset(
            s.strip().lower()
            for s in (GEO_REMOTE_ONLY_EXEMPT if exempt_regions is None else exempt_regions).split(",")
            if s.strip()
        ),
    )


def _region_for_location(location: str) -> str | None:
    """Which configured region a job's location string belongs to, or None.

    Fails open (None -> no geographic rejection) when the location can't be
    resolved, matching Rule 0's existing choice not to penalise a missing
    posted_date. Throwing away a row because a scraper gave us a location
    string we don't recognise would be the wrong kind of strict.
    """
    loc = f" {location.lower()} "
    for region, tokens in REGION_LOCATION_TOKENS.items():
        if any(tok in loc for tok in tokens):
            return region
    return None


def _region_is_remote_only(region: str, profile: PrefilterProfile) -> bool:
    """Does the user accept `region` only remotely, and do we enforce it?

    The exemption is the behaviour-preservation carve-out documented on
    GEO_REMOTE_ONLY_EXEMPT; everything not exempt is enforced.
    """
    if region in profile.exempt_regions:
        return False
    return region in profile.remote_only_regions


def _job_relevance_rank(job: dict, profile: PrefilterProfile) -> tuple:
    """Rank a job for the MAX_JOBS_PER_RUN cutoff — higher is better.

    Primary key is relevance overlap (the same signals Rule 3 uses to
    admit/reject, just used here to order rather than gate), tie-broken by
    freshness (lower age wins) so that among equally-relevant jobs the most
    recently posted one survives the cut. Age ties broken to 0 when
    unavailable — never rank a missing-date job LAST by treating "unknown"
    as "old"; Rule 0 already chose not to penalize missing posted_date, and
    ranking should stay consistent with that.

    Both of Rule 3's signals count here, not just the tech one. Ranking on
    tech overlap alone scored every non-IT JD at 0, so a non-IT user whose run
    exceeded MAX_JOBS_PER_RUN had their surviving 150 chosen by nothing at
    all — a silent failure with no reject reason and no log line to notice it
    by. Query-phrase hits give every domain something to sort on.
    """
    desc = job.get("description") or ""
    overlap = len(_extract_tech_keywords(desc) & profile.skills)
    overlap += len(_query_phrase_overlap(desc.lower(), profile.query_phrases))
    age = _job_age_days(job)
    return (overlap, -(age if age is not None else 0.0))


def _richness_score(job: dict) -> tuple:
    """Score a job dict by data richness for tie-breaking during dedup.

    Returns a tuple (desc_len, field_count, last_seen) so that max() picks
    the version with the longest description, most populated fields, and
    most recent scrape timestamp.
    """
    desc_len = len(job.get("description", "") or "")
    field_count = sum(1 for v in job.values() if v is not None and v != "")
    last_seen = job.get("last_seen", "") or job.get("scraped_at", "") or ""
    return (desc_len, field_count, last_seen)


def should_skip_cross_run(existing_job: dict | None, max_age_days: int = 7) -> bool:
    """Check if job was scored recently enough to skip re-scoring."""
    if not existing_job:
        return False
    scored_at = existing_job.get("scored_at")
    if not scored_at:
        return False
    scored_dt = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
    return (datetime.now(scored_dt.tzinfo) - scored_dt) < timedelta(days=max_age_days)


def cross_run_check(existing_job: dict | None, max_age_days: int = 7) -> dict:
    """Check if job was recently processed. Returns reuse instructions."""
    if not existing_job or not should_skip_cross_run(existing_job, max_age_days):
        return {"skip_scoring": False, "skip_tailoring": False, "reuse_artifacts": {}}
    return {
        "skip_scoring": True,
        "skip_tailoring": True,
        "reuse_artifacts": {
            "base_ats_score": existing_job.get("base_ats_score"),
            "base_hm_score": existing_job.get("base_hm_score"),
            "base_tr_score": existing_job.get("base_tr_score"),
            "tailored_ats_score": existing_job.get("tailored_ats_score"),
            "tailored_hm_score": existing_job.get("tailored_hm_score"),
            "tailored_tr_score": existing_job.get("tailored_tr_score"),
            "resume_s3_url": existing_job.get("resume_s3_url"),
            "cover_letter_s3_url": existing_job.get("cover_letter_s3_url"),
            "writing_quality_score": existing_job.get("writing_quality_score"),
        },
    }


def _job_age_days(job: dict, now: datetime | None = None) -> float | None:
    """Days since the job was posted, or None when posted_date is missing.

    Centralised so the freshness rule and any future "fresh-only" sort can
    share the same parsing rules.
    """
    posted = job.get("posted_date")
    if not posted:
        return None
    s = posted.replace("Z", "+00:00") if isinstance(posted, str) else posted
    try:
        dt = datetime.fromisoformat(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def _query_phrases(queries: list) -> frozenset:
    """Turn the user's own search queries into phrase-level match signals:
    consecutive word pairs, e.g. "Site Reliability Engineer" ->
    {"site reliability", "reliability engineer"}. A single-word query keeps
    its one word (nothing to pair it with).

    Phrases, not lone words, on purpose. An earlier version of this
    function matched individual query words directly and was verified
    (2026-09-27, live jobs_raw corpus, ~13.6k rows) to raise the real
    production user's admit rate from 13.3% to 17.4% -- almost entirely
    noise: "full" from "Full Stack Engineer" matching plain "Full Time"/
    "Full-time" employment-type boilerplate, and "engineer" alone matching
    nearly every posting in an all-tech pool. A two-word phrase match is a
    far more specific, low-noise signal than either word alone.
    """
    phrases: set = set()
    for q in queries:
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", q) if len(w) > 2]
        if len(words) == 1:
            phrases.add(words[0])
        for i in range(len(words) - 1):
            phrases.add(f"{words[i]} {words[i + 1]}")
    return frozenset(phrases)


def _query_phrase_overlap(desc_lower: str, query_phrases: frozenset) -> set:
    """Which of the user's own query phrases appear in the JD text.

    Domain-neutral counterpart to `_extract_tech_keywords`, which only
    recognises a closed software vocabulary (python, kubernetes, ...) and
    therefore can never find a match for a non-IT JD -- a nurse's or
    accountant's job description simply doesn't contain any of those
    patterns. This checks the JD against whatever the user actually
    searched for instead, so it works for any profession. Hyphens/slashes
    in the JD ("full-stack", "on-site") are normalised to spaces so they
    still match a space-joined query phrase.
    """
    if not query_phrases:
        return set()
    normalized = re.sub(r"[-/]", " ", desc_lower)
    hits = set()
    for phrase in query_phrases:
        # Escape each word separately, THEN join with \s+ -- re.escape()
        # itself escapes plain spaces (it's conservative about re.VERBOSE),
        # so escaping the whole phrase first and patching afterwards would
        # leave a stray literal backslash in the pattern instead of \s+.
        pattern = r"\s+".join(re.escape(w) for w in phrase.split(" "))
        if re.search(r"\b" + pattern + r"\b", normalized):
            hits.add(phrase)
    return hits


def _title_reject_reason(title: str, profile: PrefilterProfile) -> str | None:
    """Rule 1. Returns a reject reason, or None to admit.

    Three checks, each with its own source of truth (see the vocabulary
    section at the top of this module). Ordered cheapest-and-most-absolute
    first. Every check except the structural one honours Principle A: a term
    the user searched for cannot reject them.
    """
    # (1) Not a job posting at all. Universal, and NOT cancellable -- a
    # posting for a conference is not a job no matter what you searched for.
    structural = _STRUCTURAL_REJECT_PATTERN.search(title)
    if structural:
        return f"not_a_posting:{structural.group(1).lower()}"

    # (2) Seniority / track, from the user's experience_levels.
    for tier in profile.excluded_tiers:
        match = _SENIORITY_TIER_PATTERNS[tier].search(title)
        if match and not profile.cancels(match.group(1)):
            return f"role_mismatch:{match.group(1).lower()}"

    # (3) Internships -- an independent axis, never inferred from level.
    if not profile.include_internships:
        match = _INTERNSHIP_PATTERN.search(title)
        if match and not profile.cancels(match.group(1)):
            return f"role_mismatch:{match.group(1).lower()}"

    # (4) Off-function, for IT users only. This is the check that used to
    # reject a finance user's Financial Controller and an architect's Project
    # Architect: not because the terms were wrong for an SRE, but because
    # they were applied to everybody.
    if profile.is_domain_tech:
        match = _IT_OFF_FUNCTION_PATTERN.search(title)
        if match and not profile.cancels(match.group(1)):
            return f"role_mismatch:{match.group(1).lower()}"

    return None


def _prefilter_job(
    job: dict,
    profile: PrefilterProfile,
    max_age_days: int = JOB_MAX_AGE_DAYS,
) -> tuple[bool, str]:
    """Apply relevance pre-filter. Returns (pass, reason).

    Freshness (rule 0) runs first — cheapest to check and the most user-
    impactful (a stale posting wastes every downstream cycle).

    `profile` carries every vocabulary this function consults; build it with
    build_prefilter_profile() from the user's own search config. It is
    required rather than defaulted on purpose: a default would have to encode
    somebody's domain, and silently encoding the owner's is the bug this
    signature exists to prevent.
    """
    title = (job.get("title") or "").lower()
    desc = job.get("description") or ""
    location = (job.get("location") or "").lower()

    # Rule 0: Freshness — apply ASAP from posting is the entire goal
    age = _job_age_days(job)
    if age is not None and age > max_age_days:
        return False, f"stale:{int(age)}d_old"

    # Rule 1: Title gate — structural, seniority, internship, off-function
    reason = _title_reject_reason(title, profile)
    if reason:
        return False, reason

    # Rule 2: Description quality gate
    if len(desc) < 200:
        return False, "description_too_short"

    # Rule 3: Minimum skill overlap. Two independent signals, either one
    # sufficient: the tech-keyword vocabulary (>=2 matches, and only for a
    # user whose queries are IT-shaped -- see PrefilterProfile.skills), OR at
    # least one of the user's own query phrases showing up in the JD
    # (domain-neutral: works for "registered nurse" exactly as it does for
    # "site reliability engineer"). A single PHRASE match (not a lone word --
    # see _query_phrases) is the bar, not >=2, because a two-word literal
    # phrase match is already a precise, low-noise signal on its own.
    # Fails OPEN on a profile with no signal at all. Before this refactor
    # user_skills always started as DEFAULT_USER_SKILLS, so a user whose
    # config row was missing or had empty `queries` still cleared this rule
    # via the tech vocabulary. With that IT default gone, such a profile has
    # nothing to match on -- and gating on it would reject 100% of the pool,
    # which on a live pipeline is an outage rather than a filter. Consistent
    # with Rule 0 passing a missing posted_date and _region_for_location
    # failing open: an input we know nothing about is not evidence of
    # irrelevance. MAX_JOBS_PER_RUN still bounds what reaches scoring.
    desc_lower = desc.lower()
    if profile.skills or profile.query_phrases:
        tech_overlap = _extract_tech_keywords(desc) & profile.skills
        phrase_overlap = _query_phrase_overlap(desc_lower, profile.query_phrases)
        if len(tech_overlap) < 2 and not phrase_overlap:
            return False, f"skill_overlap:{len(tech_overlap)}"

    # Rule 4: Location compatibility. Which regions are remote-only is the
    # user's own geo_regions config; which cities belong to which region is
    # geography. See the Rule 4 section at the top of this module for why
    # those two were separated.
    region = _region_for_location(location)
    if region and "remote" not in location and _region_is_remote_only(region, profile):
        return False, f"incompatible_location:{region}_in_office"

    return True, "pass"


def handler(event, context):
    db = get_supabase()
    pipeline_run_id = event.get("pipeline_run_id", "")
    user_id = event.get("user_id", "")
    today = datetime.now(timezone.utc).date().isoformat()

    # --- Source 1: Read from scrape_runs (Fargate tasks) ---
    fargate_hashes = []
    if pipeline_run_id:
        runs = db.table("scrape_runs").select("source, status, new_job_hashes") \
            .eq("pipeline_run_id", pipeline_run_id).execute()
        for run in (runs.data or []):
            if run.get("new_job_hashes"):
                fargate_hashes.extend(run["new_job_hashes"])
        logger.info(f"[merge_dedup] scrape_runs: {len(fargate_hashes)} hashes from Fargate tasks")

    # --- Source 2: Get today's scraped jobs from jobs_raw ---
    result = db.table("jobs_raw").select("job_hash, title, company, source, description, location, posted_date") \
        .gte("scraped_at", today).execute()

    all_jobs = result.data or []

    # --- Source 3: Catch unscored jobs from recent days (backfill) ---
    # If the pipeline failed mid-run or was offline, jobs sit in jobs_raw
    # but never make it to the scored 'jobs' table. Pick them up within
    # BACKFILL_LOOKBACK_DAYS (see its own comment above for why 30, not 7,
    # and why widening this doesn't let stale postings through).
    lookback = (datetime.now(timezone.utc).date() - timedelta(days=BACKFILL_LOOKBACK_DAYS)).isoformat()
    recent = db.table("jobs_raw").select("job_hash, title, company, source, description, location, posted_date") \
        .gte("scraped_at", lookback).lt("scraped_at", today).execute()
    if recent.data:
        today_hashes = {j["job_hash"] for j in all_jobs}
        backfill = [j for j in recent.data if j["job_hash"] not in today_hashes]
        if backfill:
            all_jobs.extend(backfill)
            logger.info(f"[merge_dedup] Backfill: {len(backfill)} unscored jobs from last {BACKFILL_LOOKBACK_DAYS} days")

    if not all_jobs and not fargate_hashes:
        return {"new_job_hashes": [], "total_new": 0, "filtered_out": 0}

    # If we have specific hashes from this pipeline run, scope to those
    # This prevents processing stale jobs from previous runs
    if fargate_hashes:
        fargate_set = set(fargate_hashes)
        scoped = [j for j in all_jobs if j["job_hash"] in fargate_set]
        if scoped:
            all_jobs = scoped
            logger.info(f"[merge_dedup] Scoped to {len(all_jobs)} jobs from this pipeline run")

    # --- Tier 1: Exact hash dedup (already done during scraping, but verify) ---
    by_hash = {}
    for job in all_jobs:
        h = job["job_hash"]
        existing = by_hash.get(h)
        if not existing or _richness_score(job) > _richness_score(existing):
            by_hash[h] = job

    # --- Tier 0: Exact normalized (company+title) dedup (cross-source) ---
    # Same job from LinkedIn and Indeed has different descriptions → different job_hash.
    # This tier catches those by ignoring description entirely.
    from utils.canonical_hash import normalize_company, normalize_whitespace
    by_dedup_key = {}
    for job in by_hash.values():
        norm_company = normalize_company(job.get("company", ""))
        norm_title = normalize_whitespace(job.get("title", "")).lower()
        dedup_key = f"{norm_company}|{norm_title}"
        existing = by_dedup_key.get(dedup_key)
        if not existing or _richness_score(job) > _richness_score(existing):
            by_dedup_key[dedup_key] = job

    logger.info(f"[merge_dedup] Tier 0: {len(by_hash)} → {len(by_dedup_key)} (exact company+title)")

    # --- Tier 2: Fuzzy title+company dedup (catches remaining near-matches) ---
    seen_fuzzy = {}
    for job in by_dedup_key.values():
        norm_title = _normalize_title(job.get("title", ""))
        norm_company = job.get("company", "").lower().strip()
        fuzzy_key = f"{norm_company}|{norm_title}"

        matched = False
        for existing_key, existing_job in seen_fuzzy.items():
            ex_company, ex_title = existing_key.split("|", 1)
            if _fuzzy_match(norm_company, ex_company, 0.75) and _fuzzy_match(norm_title, ex_title, 0.65):
                # Keep the richest version
                if _richness_score(job) > _richness_score(existing_job):
                    seen_fuzzy[existing_key] = job
                matched = True
                break

        if not matched:
            seen_fuzzy[fuzzy_key] = job

    unique_jobs = list(seen_fuzzy.values())

    # --- Pre-filter: relevance check ---
    # Build this user's prefilter vocabularies from their own search config.
    # select("*") rather than naming columns: get_search_config already
    # degrades this way (PR #100), and a column that hasn't been migrated yet
    # must not take the whole step down -- build_prefilter_profile treats
    # every absent field as "no gate".
    profile = build_prefilter_profile()
    if user_id:
        try:
            config = db.table("user_search_configs").select("*").eq("user_id", user_id).execute()
            row = config.data[0] if config.data else {}
            profile = build_prefilter_profile(
                queries=row.get("queries") or [],
                experience_levels=row.get("experience_levels") or [],
                geo_regions=row.get("geo_regions") or [],
                locations=_config_locations(row.get("locations")),
                include_internships=bool(row.get("include_internships", False)),
            )
        except Exception:
            logger.warning(
                "[merge_dedup] could not load search config for user; "
                "running with an ungated profile", exc_info=True
            )
    logger.info(
        f"[merge_dedup] prefilter profile: domain_tech={profile.is_domain_tech} "
        f"skills={len(profile.skills)} phrases={len(profile.query_phrases)} "
        f"excluded_tiers={sorted(profile.excluded_tiers)} "
        f"remote_only={sorted(profile.remote_only_regions)} "
        f"geo_policy={profile.geo_policy_source}"
    )
    if profile.geo_policy_source == "legacy_fallback":
        logger.warning(
            "[merge_dedup] no geographic preference in this user's config -- "
            "falling back to LEGACY_REMOTE_ONLY_REGIONS, a single-tenant "
            "default. Populate geo_regions[].remote_only or locations to retire it."
        )

    filtered_jobs = []
    filtered_out = 0
    for job in unique_jobs:
        passes, reason = _prefilter_job(job, profile)
        if passes:
            filtered_jobs.append(job)
        else:
            filtered_out += 1
            logger.debug(f"[pre-filter] Rejected: {job.get('title')} — {reason}")

    # --- Check which are truly new (not already scored for this user) ---
    existing_hashes = set()
    existing_dedup_keys: set[str] = set()
    if user_id:
        existing = db.table("jobs").select("job_hash, company, title").eq("user_id", user_id) \
            .not_.is_("job_hash", "null").execute()
        for j in (existing.data or []):
            existing_hashes.add(j["job_hash"])
            # Build cross-query dedup key: same company+title already in jobs table
            norm_co = normalize_company(j.get("company", ""))
            norm_ti = normalize_whitespace(j.get("title", "")).lower()
            existing_dedup_keys.add(f"{norm_co}|{norm_ti}")

    def _new_job_dedup_key(job: dict) -> str:
        return f"{normalize_company(job.get('company', ''))}|{normalize_whitespace(job.get('title', '')).lower()}"

    cross_query_skipped = 0
    batch_dedup_skipped = 0
    new_jobs: list[dict] = []
    batch_dedup_keys: set[str] = set()  # Track within current batch too
    for j in filtered_jobs:
        if j["job_hash"] in existing_hashes:
            continue
        key = _new_job_dedup_key(j)
        if key in existing_dedup_keys:
            cross_query_skipped += 1
            continue
        if key in batch_dedup_keys:
            batch_dedup_skipped += 1
            logger.debug(f"[batch dedup] Skipping duplicate in batch: '{j.get('title')}' @ '{j.get('company')}'")
            continue
        # Tier 4: semantic. Catches the same posting reworded across queries.
        if os.environ.get("SEMANTIC_DEDUP", "off") == "on":
            from retrieval.dedup import find_semantic_duplicate
            duplicate = find_semantic_duplicate(j)
            if duplicate:
                filtered_out += 1
                continue
        batch_dedup_keys.add(key)
        new_jobs.append(j)

    if batch_dedup_skipped:
        logger.info(f"[merge_dedup] Batch dedup: skipped {batch_dedup_skipped} within-batch duplicates")

    if cross_query_skipped:
        logger.info(f"[merge_dedup] Cross-query dedup: skipped {cross_query_skipped} jobs already in jobs table")

    # --- Hard capacity cap (see MAX_JOBS_PER_RUN comment) ---
    # Deliberately kept separate from `filtered_out`: that field means "did
    # not pass the relevance/quality rules", this is "passed everything but
    # there wasn't scoring capacity for it today" — a different reason a
    # future reader (or self_improver.py) shouldn't have to disentangle from
    # the logs after the fact.
    capacity_capped = 0
    if len(new_jobs) > MAX_JOBS_PER_RUN:
        capacity_capped = len(new_jobs) - MAX_JOBS_PER_RUN
        new_jobs.sort(key=lambda j: _job_relevance_rank(j, profile), reverse=True)
        new_jobs = new_jobs[:MAX_JOBS_PER_RUN]
        logger.info(
            f"[merge_dedup] Capacity cap: {len(new_jobs) + capacity_capped} passed the relevance filter, "
            f"MAX_JOBS_PER_RUN={MAX_JOBS_PER_RUN} — dropped the {capacity_capped} lowest skill-overlap matches"
        )

    new_hashes = [j["job_hash"] for j in new_jobs]

    logger.info(
        f"[merge_dedup] {len(all_jobs)} scraped → {len(unique_jobs)} unique "
        f"→ {len(filtered_jobs)} passed filter ({filtered_out} filtered) "
        f"→ {len(new_hashes)} new for scoring"
    )
    return {
        "new_job_hashes": new_hashes,
        "total_new": len(new_hashes),
        "filtered_out": filtered_out,
        "capacity_capped": capacity_capped,
    }
