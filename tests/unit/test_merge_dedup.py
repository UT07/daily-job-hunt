"""Unit tests for merge_dedup Lambda."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Legacy-semantics bridge. Every test ABOVE this line was written against the
# pre-2026-09-28 prefilter, when the title/skill/location vocabularies were
# module-level constants applied to every user. They are kept verbatim as the
# behaviour-preservation guard for that refactor: same titles, same
# assertions, only the call expression changed.
#
# This helper encodes ONE user's domain (IT, entry/mid ceiling, no
# internships, India in-office excluded) -- which is exactly what was removed
# from merge_dedup, so it belongs here in the tests and not in production.
# ---------------------------------------------------------------------------

def _legacy_profile(skills, query_phrases=frozenset()):
    """A profile reproducing the pre-refactor always-on vocabularies."""
    import dataclasses

    import merge_dedup
    base = merge_dedup.build_prefilter_profile(
        experience_levels=["mid_level"],
        geo_regions=[{"name": "India", "remote_only": True}],
        exempt_regions="",
    )
    return dataclasses.replace(
        base,
        skills=frozenset(skills),
        query_phrases=frozenset(query_phrases),
        is_domain_tech=True,
    )


# Search-config row for handler tests whose assertions depend on the prefilter
# vocabularies (the seniority ceiling, or skill overlap for the capacity-cap
# ranking). Before 2026-09-28 these came from module-level IT constants and the
# tests did not have to say which user they were about; now they do, which is
# the point of the refactor.
_IT_SEARCH_CONFIG = [{
    "queries": ["Python Developer", "Site Reliability Engineer"],
    "experience_levels": ["mid_level"],
    "geo_regions": [{"name": "Ireland", "remote_only": False}],
}]


def _make_supabase(jobs_raw_data=None, existing_jobs_data=None, scrape_runs_data=None, search_config_data=None):
    """Build a mock Supabase client for merge_dedup tests."""
    mock_client = MagicMock()

    raw_result = MagicMock()
    raw_result.data = jobs_raw_data if jobs_raw_data is not None else []

    existing_result = MagicMock()
    existing_result.data = existing_jobs_data if existing_jobs_data is not None else []

    runs_result = MagicMock()
    runs_result.data = scrape_runs_data if scrape_runs_data is not None else []

    config_result = MagicMock()
    config_result.data = search_config_data if search_config_data is not None else []

    raw_chain = MagicMock()
    raw_chain.select.return_value = raw_chain
    raw_chain.gte.return_value = raw_chain
    raw_chain.execute.return_value = raw_result

    existing_chain = MagicMock()
    existing_chain.select.return_value = existing_chain
    existing_chain.eq.return_value = existing_chain
    existing_chain.not_ = existing_chain
    existing_chain.is_.return_value = existing_chain
    existing_chain.execute.return_value = existing_result

    runs_chain = MagicMock()
    runs_chain.select.return_value = runs_chain
    runs_chain.eq.return_value = runs_chain
    runs_chain.execute.return_value = runs_result

    config_chain = MagicMock()
    config_chain.select.return_value = config_chain
    config_chain.eq.return_value = config_chain
    config_chain.execute.return_value = config_result

    def table_side_effect(name):
        if name == "jobs_raw":
            return raw_chain
        elif name == "jobs":
            return existing_chain
        elif name == "scrape_runs":
            return runs_chain
        elif name == "user_search_configs":
            return config_chain
        return MagicMock()

    mock_client.table.side_effect = table_side_effect
    return mock_client


# Long enough description with tech keywords to pass pre-filter
_GOOD_DESC = "We are looking for a Python developer with experience in AWS, Kubernetes, and Docker. The role involves building microservices and CI/CD pipelines for our cloud-native platform. You will work with React frontends and FastAPI backends."


def test_dedup_keeps_richest_version():
    """When two jobs have same company+title, keeps the one with the longer description."""
    jobs_raw = [
        {
            "job_hash": "hash-short",
            "title": "Python Developer",
            "company": "Acme",
            "source": "linkedin",
            "description": _GOOD_DESC[:100],
            "location": "Dublin",
        },
        {
            "job_hash": "hash-long",
            "title": "Python Developer",
            "company": "Acme",
            "source": "indeed",
            "description": _GOOD_DESC,
            "location": "Dublin",
        },
    ]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    # Only one unique key (same company+title), and it should be the richer one
    assert result["total_new"] == 1
    assert "hash-long" in result["new_job_hashes"]
    assert "hash-short" not in result["new_job_hashes"]


def test_empty_scrape_returns_empty_list():
    """When jobs_raw returns no rows today, result is empty."""
    db = _make_supabase(jobs_raw_data=[], existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["new_job_hashes"] == []
    assert result["total_new"] == 0


def test_filters_out_already_scored_jobs():
    """Jobs whose hash already exists in the jobs table are excluded."""
    jobs_raw = [
        {
            "job_hash": "already-seen",
            "title": "Backend Engineer",
            "company": "Foo Corp",
            "source": "hn",
            "description": _GOOD_DESC,
            "location": "Dublin",
        },
        {
            "job_hash": "brand-new",
            "title": "Frontend Engineer",
            "company": "Bar Inc",
            "source": "yc",
            "description": _GOOD_DESC,
            "location": "Dublin",
        },
    ]
    existing_jobs = [{"job_hash": "already-seen"}]

    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=existing_jobs)

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert "brand-new" in result["new_job_hashes"]
    assert "already-seen" not in result["new_job_hashes"]
    assert result["total_new"] == 1


def test_prefilter_rejects_too_senior():
    """Director-level titles should be filtered out."""
    jobs_raw = [
        {
            "job_hash": "director-role",
            "title": "Director of Engineering",
            "company": "BigCorp",
            "source": "linkedin",
            "description": _GOOD_DESC,
            "location": "Dublin",
        },
    ]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[],
                        search_config_data=_IT_SEARCH_CONFIG)

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == 0
    assert result["filtered_out"] == 1


def test_prefilter_rejects_short_descriptions():
    """Jobs with descriptions < 100 chars should be filtered."""
    jobs_raw = [
        {
            "job_hash": "short-desc",
            "title": "Software Engineer",
            "company": "TinyCorp",
            "source": "linkedin",
            "description": "Short.",
            "location": "Dublin",
        },
    ]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == 0
    assert result["filtered_out"] == 1


# ── should_skip_cross_run tests ──


def test_should_skip_cross_run_recent_job():
    """Job scored 3 days ago should be skipped (within 7-day window)."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    existing = {"scored_at": scored_at}
    assert merge_dedup.should_skip_cross_run(existing) is True


def test_should_skip_cross_run_old_job():
    """Job scored 8 days ago should NOT be skipped (outside 7-day window)."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    existing = {"scored_at": scored_at}
    assert merge_dedup.should_skip_cross_run(existing) is False


def test_should_skip_cross_run_no_existing():
    """No existing job returns False."""
    import merge_dedup
    assert merge_dedup.should_skip_cross_run(None) is False


def test_should_skip_cross_run_no_scored_at():
    """Existing job with no scored_at returns False."""
    import merge_dedup
    existing = {"title": "Engineer"}
    assert merge_dedup.should_skip_cross_run(existing) is False


def test_should_skip_cross_run_z_suffix():
    """scored_at with Z suffix is parsed correctly."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = {"scored_at": scored_at}
    assert merge_dedup.should_skip_cross_run(existing) is True


def test_should_skip_cross_run_custom_max_age():
    """Custom max_age_days is respected."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    existing = {"scored_at": scored_at}
    # 3 days old, max_age=2 -> should NOT skip
    assert merge_dedup.should_skip_cross_run(existing, max_age_days=2) is False
    # 3 days old, max_age=5 -> should skip
    assert merge_dedup.should_skip_cross_run(existing, max_age_days=5) is True


# ── cross_run_check tests ──


def test_cross_run_check_reuses_artifacts_for_recent_job():
    """Recent job returns skip_scoring=True with all artifact fields populated."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    existing = {
        "scored_at": scored_at,
        "base_ats_score": 85,
        "base_hm_score": 80,
        "base_tr_score": 78,
        "tailored_ats_score": 92,
        "tailored_hm_score": 88,
        "tailored_tr_score": 90,
        "resume_s3_url": "s3://bucket/resume.pdf",
        "cover_letter_s3_url": "s3://bucket/cover.pdf",
        "writing_quality_score": 87,
    }
    result = merge_dedup.cross_run_check(existing)
    assert result["skip_scoring"] is True
    assert result["skip_tailoring"] is True
    assert result["reuse_artifacts"]["base_ats_score"] == 85
    assert result["reuse_artifacts"]["tailored_ats_score"] == 92
    assert result["reuse_artifacts"]["resume_s3_url"] == "s3://bucket/resume.pdf"
    assert result["reuse_artifacts"]["cover_letter_s3_url"] == "s3://bucket/cover.pdf"
    assert result["reuse_artifacts"]["writing_quality_score"] == 87


def test_cross_run_check_does_not_skip_old_job():
    """Old job (>7 days) returns skip_scoring=False with empty artifacts."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    existing = {
        "scored_at": scored_at,
        "base_ats_score": 85,
        "resume_s3_url": "s3://bucket/resume.pdf",
    }
    result = merge_dedup.cross_run_check(existing)
    assert result["skip_scoring"] is False
    assert result["skip_tailoring"] is False
    assert result["reuse_artifacts"] == {}


def test_cross_run_check_no_existing_job():
    """None existing job returns skip_scoring=False."""
    import merge_dedup
    result = merge_dedup.cross_run_check(None)
    assert result["skip_scoring"] is False
    assert result["skip_tailoring"] is False
    assert result["reuse_artifacts"] == {}


def test_cross_run_check_missing_artifact_fields():
    """Recent job with missing artifact fields returns None for those keys."""
    import merge_dedup
    scored_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    existing = {"scored_at": scored_at, "base_ats_score": 75}
    result = merge_dedup.cross_run_check(existing)
    assert result["skip_scoring"] is True
    assert result["reuse_artifacts"]["base_ats_score"] == 75
    assert result["reuse_artifacts"]["resume_s3_url"] is None
    assert result["reuse_artifacts"]["writing_quality_score"] is None


# ---------------------------------------------------------------------------
# Freshness pre-filter (Rule 0) — rejects jobs whose posted_date is older
# than JOB_MAX_AGE_DAYS at scrape time. Prevents "scraped today but
# posted 3 weeks ago" wasting downstream cycles.
# ---------------------------------------------------------------------------

def _fresh_job_with_posted(days_ago, **extra):
    """Build a minimal-but-passing job dict with a posted_date N days old."""
    posted = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    base = {
        "title": "Software Engineer",
        # Long-enough description with skill keywords to clear other rules
        "description": ("We use python and aws every day building cloud "
                        "services for our customers. " * 15),
        "location": "Dublin, Ireland",
        "posted_date": posted,
    }
    base.update(extra)
    return base


def test_prefilter_passes_fresh_job():
    import merge_dedup
    job = _fresh_job_with_posted(days_ago=2)
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile(user_skills))
    assert passes is True
    assert reason == "pass"


def test_prefilter_rejects_stale_job():
    import merge_dedup
    # Default max_age_days = 14
    job = _fresh_job_with_posted(days_ago=30)
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile(user_skills))
    assert passes is False
    assert reason.startswith("stale:")
    assert "30d_old" in reason


def test_prefilter_passes_job_with_null_posted_date():
    """Sources that don't supply posted_date pass this rule — we don't
    reject for missing data. Other rules still apply."""
    import merge_dedup
    job = _fresh_job_with_posted(days_ago=0)
    job["posted_date"] = None
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile(user_skills))
    assert passes is True


def test_prefilter_freshness_runs_before_other_rules():
    """Stale job fails with 'stale:Xd_old' even when it would also fail
    seniority/description/skill rules. Freshness is rule 0 (cheapest)."""
    import merge_dedup
    job = _fresh_job_with_posted(
        days_ago=99,
        title="VP of Engineering",   # would fail rule 1
        description="too short",     # would fail rule 2
    )
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile({"python"}))
    assert passes is False
    assert reason.startswith("stale:")


def test_prefilter_custom_max_age():
    """Stricter age threshold rejects less-stale jobs."""
    import merge_dedup
    job = _fresh_job_with_posted(days_ago=10)
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile(user_skills), max_age_days=7)
    assert passes is False
    assert "10d_old" in reason


def test_job_age_days_handles_z_suffix():
    import merge_dedup
    job = {"posted_date": "2026-04-22T08:30:00Z"}
    age = merge_dedup._job_age_days(
        job, now=datetime(2026, 4, 25, 8, 30, 0, tzinfo=timezone.utc)
    )
    assert age is not None
    assert 2.9 < age < 3.1


def test_job_age_days_returns_none_for_missing():
    import merge_dedup
    assert merge_dedup._job_age_days({}) is None
    assert merge_dedup._job_age_days({"posted_date": None}) is None
    assert merge_dedup._job_age_days({"posted_date": "garbage"}) is None


# ---------------------------------------------------------------------------
# Tuned title pre-filter (2026-09-25 throughput fix) — Rule 1 now rejects
# off-track functions (sales, management track, data science, etc.) in
# addition to seniority, so that the ~150/day capacity target is hit by
# raising precision rather than just imposing a volume cap. Every reject
# case below was checked against real corpus titles (scripts/tune_prefilter.py)
# before being added to REJECT_TITLE_KEYWORDS / _WORD_BOUNDARY_REJECT_PATTERN.
# ---------------------------------------------------------------------------

def _good_job(title, **extra):
    """A job that would pass every rule except the one under test."""
    base = {
        "title": title,
        "description": _GOOD_DESC,
        "location": "Dublin, Ireland",
        "posted_date": datetime.now(timezone.utc).isoformat(),
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("title", [
    "Senior Sales Engineer - UK",
    "Enterprise Account Executive, Juno",
    "Business Development Representative (Sydney)",
    "Senior Solutions Consultant",
    "Presales Engineer",
    "Solutions Architect, Enterprise (Pre-sales)",
    "Senior Professional Services Engineer",
])
def test_prefilter_rejects_sales_and_presales_titles(title):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is False
    assert reason.startswith("role_mismatch:")


@pytest.mark.parametrize("title", [
    "Manager, Customer Success",
    "Senior Engineering Manager - Developer Experience",  # management track, no EM archetype exists
    "Manager, Site Reliability Engineering (SRE)",
])
def test_prefilter_rejects_manager_titles_including_engineering_manager(title):
    """Manager-track titles are rejected even when SRE/engineering-flavored:
    this pipeline's only two resume archetypes (sre_devops, fullstack) are
    both IC resumes, so an EM-track JD can't be tailored well regardless of
    how relevant its buzzwords look."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is False
    assert reason == "role_mismatch:manager"


@pytest.mark.parametrize("title", [
    "Solutions Architect, Enterprise",
    "Manager, Solutions Architects",  # plural form
    "Principal Solutions Architect",
])
def test_prefilter_rejects_architect_titles(title):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is False


@pytest.mark.parametrize("title,expected_kw", [
    ("Senior Data Scientist", "data scientist"),
    ("Senior Developer Advocate", "developer advocate"),
    ("Recruiter", "recruiter"),
    ("Compensation Analyst", "compensation analyst"),
    ("Controller", "controller"),
    ("Senior Revenue Analytics Analyst", "revenue analytics"),
])
def test_prefilter_rejects_off_archetype_titles(title, expected_kw):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is False
    assert reason == f"role_mismatch:{expected_kw}"


def test_prefilter_rejects_non_posting_with_a_structural_reason():
    """Split out of the parametrize above 2026-09-28. Still rejected, but the
    reason moved role_mismatch: -> not_a_posting:, because "this is a
    conference, not a job" is a universal fact rather than a statement about
    what role suits this user -- it is the one title vocabulary a user's own
    query cannot cancel."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _good_job("2026 - Women in Tech Summit, EMEA"), _legacy_profile({"python", "aws"})
    )
    assert passes is False
    assert reason == "not_a_posting:summit"


@pytest.mark.parametrize("title", [
    "Software Engineering Intern - Summer 2026",
    "Software Engineer, Intern (Summer or Winter)",
    "Software Engineer Internship, Frontend",
    "Data Science Intern (Winter 2027)",
])
def test_prefilter_rejects_internships(title):
    """Matches existing user feedback (feedback_graduated.md) — the user has
    already graduated, so internship postings are never worth a scoring slot."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is False
    assert reason.startswith("role_mismatch:") and "intern" in reason


# --- Collision guards: real titles the tuned filter must NOT reject -------

@pytest.mark.parametrize("title", [
    "Staff Software Engineer, International",
    "Senior Android Engineer, International",
    "Infrastructure Lead / Senior Software Engineer - Data Platforms & Internal Tooling",
])
def test_prefilter_does_not_reject_international_or_internal_titles(title):
    """Regression guard: naive substring matching on 'intern' would also
    catch 'International' and 'Internal' — both real, senior, on-target
    titles observed in the live jobs_raw corpus. Word-boundary matching
    (_WORD_BOUNDARY_REJECT_PATTERN) must not flag either."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is True, f"{title!r} was wrongly rejected: {reason}"


@pytest.mark.parametrize("title", [
    "Senior Backend Engineer, Architecture Engineering: Nonlinear Productivity",
    "Site Reliability Engineer - Video Live Streaming Architecture",
    "Staff Backend Engineer, Architecture Engineering: Nonlinear Productivity",
])
def test_prefilter_does_not_reject_architecture_team_titles(title):
    """Regression guard for the same class of bug as the intern/international
    collision: 'architect' is a substring of 'Architecture', which appears in
    real backend/SRE team names on Gitlab's board. Confirmed via
    scripts/tune_prefilter.py against the live corpus before this test was
    written — these are genuine hands-on IC roles, not pre-sales Architects."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile({"python", "aws"}))
    assert passes is True, f"{title!r} was wrongly rejected: {reason}"


def test_prefilter_does_not_reject_salesforce_titles():
    """Regression guard: 'sales' is a substring of 'Salesforce', a real
    platform-engineering title observed in the corpus (Ashby/Benchling:
    'Salesforce Engineer, Business Technology Team'). This is why
    REJECT_TITLE_KEYWORDS uses scoped phrases like 'sales engineer' instead
    of a bare 'sales' keyword."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _good_job("Salesforce Engineer, Business Technology Team"), _legacy_profile({"python", "aws"})
    )
    assert passes is True, f"wrongly rejected: {reason}"


def test_prefilter_does_not_reject_security_partnerships_title():
    """Regression guard: 'partner' is not a bare reject keyword because it
    matched 'Staff Security Engineer, Security Partnerships' — a real IC
    security role — in the live corpus scan."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _good_job("Staff Security Engineer, Security Partnerships"), _legacy_profile({"python", "aws"})
    )
    assert passes is True, f"wrongly rejected: {reason}"


# --- Recall guard: fails if a future change makes the filter too aggressive ---

@pytest.mark.parametrize("title", [
    "Senior Site Reliability Engineer",
    "DevOps Engineer",
    "Backend Engineer, Platform Team",
    "Full Stack Software Engineer",
    "Staff Software Engineer, Infrastructure",
    "Software Engineer, New Grad",
    "Cloud Infrastructure Engineer",
])
def test_prefilter_admits_realistic_good_jobs(title):
    """Recall guard, not a precision test: these are ordinary, on-target
    SRE/DevOps/fullstack titles with a solid JD (the _GOOD_DESC fixture,
    fresh posting, Dublin location) and must keep passing. If a future
    change to REJECT_TITLE_KEYWORDS / _WORD_BOUNDARY_REJECT_PATTERN /
    MIN_SKILL_OVERLAP over-broadens the filter (e.g. a bare 'engineer' or
    'staff' keyword, or raising the skill-overlap bar too high), this is the
    test that should catch it — a filter that hits its volume target by
    throwing away good, ordinary matches is worse than no filter."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), _legacy_profile(merge_dedup.DEFAULT_USER_SKILLS))
    assert passes is True, f"{title!r} was wrongly rejected: {reason}"


# ---------------------------------------------------------------------------
# Part 2 — widened backfill window (was a hardcoded 7 days; a job that
# missed it was lost permanently, not merely delayed — 2026-09-25 audit P0-4).
# ---------------------------------------------------------------------------

def test_backfill_lookback_widened_beyond_old_7_day_window():
    """Direct regression guard on the constant itself: if this ever drifts
    back down to 7, the P0-4 bug (jobs that miss the window are lost
    forever, not delayed) is silently reintroduced."""
    import merge_dedup
    assert merge_dedup.BACKFILL_LOOKBACK_DAYS > 7
    assert merge_dedup.BACKFILL_LOOKBACK_DAYS == 30


def _make_supabase_for_backfill(today_data, backfill_data, existing_jobs_data=None):
    """Mock a Supabase client where the SAME jobs_raw table is queried twice
    in one handler() run (Source 2 "today", Source 3 "backfill") with
    different .gte()/.lt() bounds. Returns today_data on the first .execute()
    call and backfill_data on the second, so the test can tell the two
    queries apart — unlike _make_supabase above, which was never exercised
    against a non-empty backfill path (MagicMock's default empty __iter__
    silently no-ops it)."""
    mock_client = MagicMock()

    raw_chain = MagicMock()
    raw_chain.select.return_value = raw_chain
    raw_chain.gte.return_value = raw_chain
    raw_chain.lt.return_value = raw_chain
    calls = {"n": 0}

    def execute_side_effect():
        calls["n"] += 1
        result = MagicMock()
        result.data = today_data if calls["n"] == 1 else backfill_data
        return result

    raw_chain.execute.side_effect = execute_side_effect

    existing_chain = MagicMock()
    existing_chain.select.return_value = existing_chain
    existing_chain.eq.return_value = existing_chain
    existing_chain.not_ = existing_chain
    existing_chain.is_.return_value = existing_chain
    existing_result = MagicMock()
    existing_result.data = existing_jobs_data or []
    existing_chain.execute.return_value = existing_result

    empty_chain = MagicMock()
    empty_result = MagicMock()
    empty_result.data = []
    empty_chain.select.return_value = empty_chain
    empty_chain.eq.return_value = empty_chain
    empty_chain.execute.return_value = empty_result

    def table_side_effect(name):
        if name == "jobs_raw":
            return raw_chain
        elif name == "jobs":
            return existing_chain
        return empty_chain

    mock_client.table.side_effect = table_side_effect
    return mock_client, raw_chain


def test_backfill_reaches_20_day_old_unscored_job():
    """A job scraped 20 days ago (inside the new 30-day window, outside the
    old 7-day one) and never scored must still be found and scored — this is
    exactly the "misses its window, lost forever" bug the widened window
    fixes. posted_date is set fresh so Rule 0 (freshness) doesn't reject it
    for an unrelated reason and mask what's being tested."""
    import merge_dedup
    old_job = _good_job("Senior Backend Engineer", job_hash="old-unscored", company="Acme")
    db, raw_chain = _make_supabase_for_backfill(today_data=[], backfill_data=[old_job])

    with patch("merge_dedup.get_supabase", return_value=db):
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert "old-unscored" in result["new_job_hashes"]
    # The backfill query's .gte() bound must reflect ~30 days, not the old 7.
    gte_calls = [c for c in raw_chain.gte.call_args_list if c.args[0] == "scraped_at"]
    assert len(gte_calls) == 2  # Source 2 (today) + Source 3 (backfill)
    lookback_arg = gte_calls[1].args[1]
    today = datetime.now(timezone.utc).date()
    lookback_date = datetime.fromisoformat(lookback_arg).date()
    assert (today - lookback_date).days == merge_dedup.BACKFILL_LOOKBACK_DAYS


# ---------------------------------------------------------------------------
# Part 1 (defense in depth) — MAX_JOBS_PER_RUN hard cap. The content filter
# above does most of the work, but a burst day can still out-produce it (the
# real 2026-04-01 corpus admits 500+ under the content filter alone — see
# the throughput fix report) — this cap is what actually guarantees
# ScoreBatchMap never receives more than capacity allows.
# ---------------------------------------------------------------------------

def _job_with_overlap(n, overlap_count, job_hash, company):
    """Build a passing job whose description mentions exactly `overlap_count`
    distinct DEFAULT_USER_SKILLS keywords, for deterministic relevance
    ranking in the cap tests."""
    keywords = ["python", "aws", "kubernetes", "docker", "react", "java"][:overlap_count]
    desc = (
        f"We are hiring for role number {n}. Our stack: " + ", ".join(keywords) + ". "
        + "We build reliable, well-tested, cloud-native systems for our customers. " * 5
    )
    return {
        "job_hash": job_hash,
        "title": f"Software Engineer {n}",
        "company": company,
        "source": "greenhouse",
        "description": desc,
        "location": "Dublin, Ireland",
        "posted_date": datetime.now(timezone.utc).isoformat(),
    }


def _distinct_company(i: int) -> str:
    """A company name for index i that won't fuzzy-match a neighboring
    index's name. f"Company{i}" would: SequenceMatcher scores "company1" vs
    "company2" as near-identical (one trailing digit differs), so Tier 2
    fuzzy dedup collapses them all into "the same job" — correct dedup
    behavior, but it defeats a test that needs N genuinely distinct jobs. A
    shared word/suffix scheme has the same problem once two indices land in
    the same word bucket (tried first; still collapsed 175 -> 60). A hash
    digest has no shared structure between neighboring indices at all, so
    SequenceMatcher ratios stay low regardless of how many jobs are
    generated."""
    import hashlib
    return hashlib.md5(str(i).encode()).hexdigest()[:12]


def test_max_jobs_per_run_caps_total_output():
    import merge_dedup
    n_jobs = merge_dedup.MAX_JOBS_PER_RUN + 25
    jobs_raw = [
        _job_with_overlap(i, overlap_count=2, job_hash=f"hash-{i}", company=_distinct_company(i))
        for i in range(n_jobs)
    ]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup as md
        result = md.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == merge_dedup.MAX_JOBS_PER_RUN
    assert len(result["new_job_hashes"]) == merge_dedup.MAX_JOBS_PER_RUN
    assert result["capacity_capped"] == n_jobs - merge_dedup.MAX_JOBS_PER_RUN


def test_max_jobs_per_run_keeps_highest_skill_overlap():
    """When the cap has to drop jobs, it must drop the LEAST relevant ones
    first, not an arbitrary/order-dependent subset — a filter that hits its
    volume target by discarding the best matches is worse than no filter."""
    import merge_dedup
    jobs_raw = [
        _job_with_overlap(1, overlap_count=2, job_hash="low-overlap", company="LowCo"),
        _job_with_overlap(2, overlap_count=4, job_hash="mid-overlap", company="MidCo"),
        _job_with_overlap(3, overlap_count=6, job_hash="high-overlap", company="HighCo"),
    ]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[],
                        search_config_data=_IT_SEARCH_CONFIG)

    with patch("merge_dedup.get_supabase", return_value=db), \
         patch("merge_dedup.MAX_JOBS_PER_RUN", 2):
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == 2
    assert set(result["new_job_hashes"]) == {"mid-overlap", "high-overlap"}
    assert "low-overlap" not in result["new_job_hashes"]
    assert result["capacity_capped"] == 1


def test_max_jobs_per_run_does_not_trigger_under_cap():
    """Fewer than MAX_JOBS_PER_RUN passing jobs: nothing is capped, and the
    field says so explicitly (0, not None/missing) for callers that log it."""
    import merge_dedup
    jobs_raw = [_job_with_overlap(1, overlap_count=2, job_hash="only-one", company="SoloCo")]
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == 1
    assert result["capacity_capped"] == 0


# ---------------------------------------------------------------------------
# Rule 3 domain-neutral fix (2026-09-27) — _extract_tech_keywords only
# recognises a closed software vocabulary (python, kubernetes, ...), so a
# genuinely relevant non-IT job description (nursing, accounting, teaching)
# always had zero overlap with it and was rejected by Rule 3 regardless of
# real relevance — the "For a non-IT user those may reject everything"
# concern this section verifies. _prefilter_job now also accepts
# query_phrases: two-word phrases built from the user's OWN search queries,
# checked directly against the JD text via _query_phrase_overlap, as an
# alternate signal. Either signal clearing its threshold is enough, so an
# IT user's existing tech-keyword-only admit behaviour is unchanged
# (query_phrases defaults to empty, matching every pre-existing call site
# above). PHRASES, not lone words -- see _query_phrases' docstring for the
# live-corpus false-positive this avoids (single query words like "full"
# and "engineer" matching unrelated "Full Time"/"Full-time" boilerplate).
# ---------------------------------------------------------------------------

_NURSE_DESC = (
    "We are looking for a Registered Nurse to join our ICU team. The role "
    "involves patient assessment, medication administration, and care "
    "planning for critically ill patients. You will work closely with "
    "physicians and the wider clinical team on a rotating shift schedule. "
    "Current NMBI registration required."
)


def test_prefilter_rejects_relevant_non_it_job_without_query_phrases():
    """Documents the pre-fix bug directly: with only the hardcoded tech
    vocabulary to check against, a real, on-target nursing job has zero
    overlap and is rejected — this is exactly why query_phrases exists."""
    import merge_dedup
    job = _good_job("Registered Nurse - ICU", description=_NURSE_DESC)
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile(merge_dedup.DEFAULT_USER_SKILLS))
    assert passes is False
    assert reason == "skill_overlap:0"


def test_prefilter_admits_non_it_job_via_query_phrase_overlap():
    """The fix: a nurse's own search query ("ICU Registered Nurse") gives
    Rule 3 a domain-neutral overlap signal, so her real jobs are no longer
    rejected just because the JD doesn't mention Python or AWS."""
    import merge_dedup
    job = _good_job("Registered Nurse - ICU", description=_NURSE_DESC)
    query_phrases = merge_dedup._query_phrases(["ICU Registered Nurse"])
    passes, reason = merge_dedup._prefilter_job(
        job, _legacy_profile(merge_dedup.DEFAULT_USER_SKILLS, query_phrases),
    )
    assert passes is True
    assert reason == "pass"


def test_prefilter_tech_path_unaffected_by_absent_query_phrases():
    """Regression guard: an IT user's existing tech-keyword-overlap admit
    behaviour is unchanged when query_phrases is left at its default —
    every pre-existing caller in this file invokes _prefilter_job without
    it."""
    import merge_dedup
    job = _good_job("Backend Engineer")
    passes, reason = merge_dedup._prefilter_job(job, _legacy_profile({"python", "aws"}))
    assert passes is True
    assert reason == "pass"


def test_query_phrases_builds_bigrams_and_keeps_single_words():
    import merge_dedup
    assert merge_dedup._query_phrases(["Site Reliability Engineer"]) == {
        "site reliability", "reliability engineer",
    }
    assert merge_dedup._query_phrases(["Nursing"]) == {"nursing"}


def test_query_phrase_overlap_matches_whole_phrases_only():
    """No substring false positives (e.g. "nurse" inside "nursery"), and no
    hyphen-formatting false negatives ("full-stack" must still match the
    "full stack" phrase)."""
    import merge_dedup
    desc = "we need someone experienced in nursery education and childcare"
    assert merge_dedup._query_phrase_overlap(desc, frozenset({"nurse"})) == set()
    assert merge_dedup._query_phrase_overlap(
        desc, frozenset({"nursery education", "childcare"})
    ) == {"nursery education", "childcare"}
    assert merge_dedup._query_phrase_overlap(
        "looking for a full-stack developer", frozenset({"full stack"})
    ) == {"full stack"}


def test_prefilter_rejects_single_generic_word_boilerplate_collision():
    """The exact false positive the phrase redesign fixes: a job that only
    shares generic, individually-common words with the user's query (here
    "full" from employment-type boilerplate, plus the near-universal
    "engineer") must NOT be admitted just because two such lone words
    happen to co-occur -- unlike a real "full stack"/"site reliability"
    phrase match, that's noise, not relevance."""
    import merge_dedup
    desc = (
        "Join our team as a Scientific Software Engineer working on "
        "simulation tooling for physics research. Full Time, on-site. "
        "We offer competitive pay and great benefits for the right "
        "candidate with a strong academic background."
    )
    job = _good_job("Scientific Software Engineer", description=desc)
    query_phrases = merge_dedup._query_phrases(["Full Stack Engineer"])
    passes, reason = merge_dedup._prefilter_job(
        job, _legacy_profile(merge_dedup.DEFAULT_USER_SKILLS, query_phrases),
    )
    assert passes is False
    assert reason == "skill_overlap:0"


def test_handler_admits_non_it_job_end_to_end_via_configured_queries():
    """Full handler() path: a user whose user_search_configs.queries are
    nursing terms gets their real nursing job admitted, not silently
    filtered down to zero results the way the pre-fix DEFAULT_USER_SKILLS-
    only overlap check would have."""
    import merge_dedup
    nurse_job = {
        "job_hash": "nurse-job-1",
        "title": "Registered Nurse - ICU",
        "company": "St. James's Hospital",
        "source": "indeed",
        "description": _NURSE_DESC,
        "location": "Dublin, Ireland",
        "posted_date": datetime.now(timezone.utc).isoformat(),
    }
    db = _make_supabase(
        jobs_raw_data=[nurse_job],
        existing_jobs_data=[],
        search_config_data=[{"queries": ["ICU Registered Nurse"]}],
    )

    with patch("merge_dedup.get_supabase", return_value=db):
        result = merge_dedup.handler({"user_id": "nurse-user"}, None)

    assert result["new_job_hashes"] == ["nurse-job-1"]
    assert result["total_new"] == 1


# ===========================================================================
# Domain-neutral prefilter (2026-09-28) — the closed vocabularies in Rule 1,
# Rule 3 and Rule 4 now derive from the user's own search config instead of a
# hardcoded IT list.
#
# Background: PR #100 (a798e4c) gave Rule 3 a domain-neutral second path but
# deliberately left REJECT_TITLE_KEYWORDS alone as "too risky to change
# without a live golden-set re-run". The consequence was that a non-IT user
# searching for their OWN target role still had it rejected before Rule 3 ever
# ran: "Financial Controller" -> role_mismatch:controller, "Marketing Manager"
# -> role_mismatch:manager, "Project Architect" -> role_mismatch:architect.
#
# The fix is a per-user profile (see build_prefilter_profile) that supplies
# every vocabulary Rule 1/3/4 consults. Two principles the tests below pin:
#   1. A term the user searched for CANNOT reject them ("your own words
#      override the blocklist"). This is what makes "Marketing Manager" work
#      without weakening "manager" for a user who never asked for it -- in
#      marketing/finance/product "Manager" is an IC seniority label, in tech
#      it's a people-management track. The word alone can't tell you which.
#   2. The IT vocabularies apply ONLY to a user whose own queries are
#      IT-shaped. A nurse or accountant doesn't get them overridden, they
#      never get loaded.
# ===========================================================================

# Realistic non-IT job descriptions — long enough to clear Rule 2's 200-char
# gate, and deliberately containing zero terms from _TECH_VOCABULARY so that
# the only thing that can admit them is the user's own query phrases.
_FINANCE_DESC = (
    "We are seeking a Financial Controller to own the month-end close, statutory "
    "reporting under IFRS, VAT returns and audit liaison. You will manage "
    "reconciliations, budgeting and forecasting cycles, and business partnering "
    "with commercial teams. ACA or ACCA qualified with strong Excel and ERP "
    "experience is required for this role."
)
_MARKETING_DESC = (
    "As Marketing Manager you will own campaign strategy, brand positioning and "
    "demand generation across paid and organic channels. You will run search "
    "engine optimisation, email nurture flows and content calendars, manage "
    "agency relationships, and report on customer acquisition cost and return "
    "on ad spend to the leadership team. Five years of B2B marketing required."
)
_ARCHITECTURE_DESC = (
    "We are hiring a Project Architect to lead residential and mixed-use schemes "
    "from concept through to construction. You will produce technical drawings, "
    "coordinate with structural and mechanical consultants, manage planning "
    "applications and run site inspections. RIAI accreditation and five years "
    "of post-part-three practice experience are required."
)
_NURSING_DESC = (
    "The ICU Registered Nurse delivers direct patient care in a twelve-bed "
    "critical care unit, managing ventilated patients, titrating vasoactive "
    "infusions, and collaborating with the multidisciplinary team. Active NMBI "
    "registration and BLS or ACLS certification are required. Rotating shifts "
    "including nights and weekends are part of this post."
)

# The production user's real config (config.yaml + user_search_configs) —
# every behaviour-preservation assertion in this section is made against THIS
# profile, so a regression in the owner's admit/reject behaviour fails loudly.
_OWNER_QUERIES = [
    "Site Reliability Engineer", "DevOps Engineer", "Platform Engineer",
    "Cloud Engineer", "Infrastructure Engineer", "SRE", "Software Engineer",
    "Full Stack Developer", "Backend Engineer", "Python Developer",
    "Graduate Software Engineer", "AWS Engineer", "Kubernetes Engineer",
    "Cloud Infrastructure Engineer",
]
_OWNER_EXPERIENCE_LEVELS = ["entry_level", "mid_level"]
_OWNER_GEO_REGIONS = [
    {"name": "Ireland", "remote_only": False},
    {"name": "India", "remote_only": True},
    {"name": "US (Remote)", "remote_only": True},
]


def _owner_profile():
    """The production user's prefilter profile, built from their real config."""
    import merge_dedup
    return merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES,
        experience_levels=_OWNER_EXPERIENCE_LEVELS,
        geo_regions=_OWNER_GEO_REGIONS,
    )


def _persona_profile(queries, experience_levels=("mid_level",), **kw):
    """A prefilter profile for an arbitrary non-IT persona."""
    import merge_dedup
    return merge_dedup.build_prefilter_profile(
        queries=list(queries), experience_levels=list(experience_levels), **kw
    )


def _jd_mentioning(role, body):
    """A JD that names the role it is advertising, the way real postings do.

    Needed because Rule 3 is a genuine relevance gate: pairing a persona's
    title with an unrelated body makes the job legitimately irrelevant, so
    the test would pass/fail on Rule 3 rather than on the Rule 1 title
    vocabulary it is meant to exercise.
    """
    return f"About the role: we are recruiting a {role} to join our team. {body}"


def _job(title, description, location="Dublin, Ireland", **extra):
    base = {
        "title": title,
        "company": "Acme",
        "description": description,
        "location": location,
        "posted_date": datetime.now(timezone.utc).isoformat(),
    }
    base.update(extra)
    return base


# --- Principle 1: a non-IT user's own target role is admitted ---------------

@pytest.mark.parametrize("title,desc,queries", [
    ("Financial Controller", _FINANCE_DESC, ["Financial Controller"]),
    ("Marketing Manager", _MARKETING_DESC, ["Marketing Manager"]),
    ("Project Architect", _ARCHITECTURE_DESC, ["Project Architect"]),
    ("ICU Registered Nurse", _NURSING_DESC, ["ICU Registered Nurse"]),
    ("Senior Data Scientist", _FINANCE_DESC, ["Data Scientist"]),
    ("Compensation Analyst", _FINANCE_DESC, ["Compensation Analyst"]),
    ("Technical Recruiter", _MARKETING_DESC, ["Technical Recruiter"]),
    ("Finance Manager", _FINANCE_DESC, ["Finance Manager"]),
    ("Brand Director", _MARKETING_DESC, ["Brand Director"]),
])
def test_prefilter_admits_non_it_persona_searching_for_that_exact_role(title, desc, queries):
    """The headline bug: every one of these is rejected today with
    role_mismatch, for a user whose search config asks for precisely it."""
    import merge_dedup
    job = _job(title, _jd_mentioning(title, desc))
    passes, reason = merge_dedup._prefilter_job(job, _persona_profile(queries))
    assert passes is True, f"{title!r} wrongly rejected for a user searching {queries!r}: {reason}"


# --- Principle 2: the owner's behaviour is unchanged ------------------------

@pytest.mark.parametrize("title", [
    "Senior Data Scientist",
    "Manager, Customer Success",
    "Senior Engineering Manager - Developer Experience",
    "Senior Sales Engineer - UK",
    "Enterprise Account Executive, Juno",
    "Director of Engineering",
    "Head of Platform",
    "Chief Technology Officer",
    "Solutions Architect, Enterprise (Pre-sales)",
    "Technical Recruiter",
    "2026 - Women in Tech Summit, EMEA",
])
def test_owner_still_rejects_every_off_target_title(title):
    """None of these appear in the owner's queries, so nothing cancels them
    and every one must stay rejected. This is the guard that the refactor
    didn't widen the owner's admit rate -- the scoring bottleneck depends on
    it (Groq free tier caps AI scoring near 80-120 jobs/day)."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_job(title, _GOOD_DESC), _owner_profile())
    assert passes is False, f"{title!r} newly ADMITTED for the owner"
    assert reason.startswith(("role_mismatch:", "not_a_posting:")), reason


@pytest.mark.parametrize("title", [
    "Site Reliability Engineer",
    "Senior DevOps Engineer",
    "Platform Engineer (Kubernetes)",
    "Software Engineer, New Grad",
    "Cloud Infrastructure Engineer",
    "Staff Software Engineer, International",
    "Senior Backend Engineer, Architecture Engineering",
])
def test_owner_still_admits_every_on_target_title(title):
    """Recall guard for the owner: the refactor must not newly reject an
    ordinary on-target title either. 'International' and 'Architecture' are
    the word-boundary collision cases from the 2026-09-25 tuning."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_job(title, _GOOD_DESC), _owner_profile())
    assert passes is True, f"{title!r} newly REJECTED for the owner: {reason}"


# --- Seniority ceiling derives from experience_levels ----------------------
# Mapping (owner-specified 2026-09-28): entry_level/mid_level exclude exec +
# management; senior excludes exec only; lead/manager exclude nothing. Senior
# deliberately still sees management-track roles -- a senior IC can judge an
# EM posting for themselves, and over-filtering is the failure mode that
# actually bites this pipeline (a near-empty dashboard).

@pytest.mark.parametrize("levels,title,expected_pass", [
    (["entry_level"], "Director of Engineering", False),
    (["entry_level"], "Engineering Manager", False),
    (["mid_level"], "Director of Engineering", False),
    (["mid_level"], "Engineering Manager", False),
    (["senior"], "Director of Engineering", False),
    (["senior"], "Engineering Manager", True),
    (["lead"], "Director of Engineering", True),
    (["manager"], "Engineering Manager", True),
    # Multiple levels: the most permissive selected level wins, otherwise
    # ticking "senior" alongside "mid_level" would be silently ignored.
    (["mid_level", "senior"], "Engineering Manager", True),
])
def test_seniority_exclusions_derive_from_experience_levels(levels, title, expected_pass):
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES, experience_levels=levels,
    )
    passes, reason = merge_dedup._prefilter_job(_job(title, _GOOD_DESC), profile)
    assert passes is expected_pass, f"{title!r} at levels={levels!r}: {reason}"


def test_no_experience_levels_configured_excludes_nothing_by_seniority():
    """An unconfigured user gets no seniority gate at all -- guessing a
    ceiling for someone who never told us their level is how a non-IT user
    ends up with an empty dashboard."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(queries=["Financial Controller"])
    passes, _ = merge_dedup._prefilter_job(
        _job("Finance Director", _FINANCE_DESC), profile
    )
    assert passes is True


# --- Internships are an independent flag, not a seniority tier -------------
# Deriving "no internships" from entry_level would be wrong: a new graduate
# legitimately wants them. The owner's real rule is narrower still ("only
# internships requiring CURRENT enrolment"), which is a JD-text condition and
# is deliberately out of scope here -- see the follow-up note in merge_dedup.

def test_internships_rejected_by_default():
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES, experience_levels=["entry_level"],
    )
    passes, reason = merge_dedup._prefilter_job(
        _job("Software Engineering Intern", _GOOD_DESC), profile
    )
    assert passes is False
    assert "intern" in reason


def test_internships_admitted_when_include_internships_set():
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES, experience_levels=["entry_level"],
        include_internships=True,
    )
    passes, reason = merge_dedup._prefilter_job(
        _job("Software Engineering Intern", _GOOD_DESC), profile
    )
    assert passes is True, reason


def test_include_internships_is_independent_of_experience_level():
    """entry_level must not imply internships, and senior must not forbid
    them -- the two axes are unrelated."""
    import merge_dedup
    senior_wants_interns = merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES, experience_levels=["senior"], include_internships=True,
    )
    passes, _ = merge_dedup._prefilter_job(
        _job("Engineering Internship Programme", _GOOD_DESC), senior_wants_interns
    )
    assert passes is True


# --- The 'cto' substring collision ----------------------------------------
# "cto" was checked as a bare substring, and "cto" is inside "director",
# "doctor", "inspector", "collector", "contractor", "refractory", "sector".
# Invisible in an all-tech corpus (the only colliding title it contains,
# "...Director", is independently rejected by "director") and therefore
# missed by the 2026-09-25 corpus tuning, which did hunt this exact class of
# bug for "sales"/"partner".

@pytest.mark.parametrize("title,desc", [
    ("Doctor", _NURSING_DESC),
    ("Junior Doctor", _NURSING_DESC),
    ("Site Inspector", _ARCHITECTURE_DESC),
    ("Debt Collector", _FINANCE_DESC),
    ("Sector Lead, Public Health", _NURSING_DESC),
])
def test_cto_substring_no_longer_swallows_unrelated_titles(title, desc):
    import merge_dedup
    profile = _persona_profile([title])
    job = _job(title, _jd_mentioning(title, desc))
    passes, reason = merge_dedup._prefilter_job(job, profile)
    assert passes is True, f"{title!r} rejected by a 'cto' substring collision: {reason}"


@pytest.mark.parametrize("title", ["Chief Technology Officer", "CTO", "CTO / Co-Founder", "Group CIO"])
def test_cto_still_rejected_as_a_whole_word_for_the_owner(title):
    """Fixing the collision must not stop 'CTO' rejecting an actual CTO
    posting -- word-boundary matching, not removal."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_job(title, _GOOD_DESC), _owner_profile())
    assert passes is False, f"{title!r} newly admitted: {reason}"


# --- IT vocabularies are not loaded at all for a non-IT persona ------------

def test_non_it_persona_skills_contain_no_tech_vocabulary():
    """DEFAULT_USER_SKILLS used to be the unconditional base of user_skills,
    so a nurse's skill set contained 'kubernetes'. Harmless for Rule 3, but
    _job_relevance_rank ranks by exactly this overlap -- which made every
    non-IT JD tie at 0 and the MAX_JOBS_PER_RUN cut arbitrary."""
    profile = _persona_profile(["ICU Registered Nurse"])
    assert not (profile.skills & {"kubernetes", "python", "aws", "terraform"})
    assert "nurse" in profile.skills


def test_it_persona_still_gets_the_tech_vocabulary():
    profile = _owner_profile()
    assert {"python", "aws", "kubernetes"} <= profile.skills
    assert profile.is_domain_tech is True


def test_relevance_rank_orders_non_it_jobs_by_query_phrase_overlap():
    """The cap-cutting rank must be able to tell two non-IT JDs apart."""
    import merge_dedup
    profile = _persona_profile(["ICU Registered Nurse", "Critical Care Nurse"])
    on_target = {"description": _NURSING_DESC}
    off_target = {"description": _FINANCE_DESC}
    assert (merge_dedup._job_relevance_rank(on_target, profile)
            > merge_dedup._job_relevance_rank(off_target, profile))


# --- Rule 4: location policy derives from geo_regions ---------------------
# The India city list survives as GEOGRAPHY REFERENCE DATA ("Bangalore is in
# India" -- a fact true for every user). The *decision* to exclude in-office
# India roles moves to the user's own geo_regions[...].remote_only flag.

def test_in_office_job_in_a_remote_only_region_is_rejected():
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Bangalore, India"),
        _owner_profile(),
    )
    assert passes is False
    assert reason.startswith("incompatible_location:")


def test_remote_job_in_a_remote_only_region_is_admitted():
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Bangalore, India (Remote)"),
        _owner_profile(),
    )
    assert passes is True, reason


def test_bare_city_name_still_resolves_to_its_region():
    """Scrapers frequently emit a bare city with no country ('Dublin',
    'Bangalore'), so region matching cannot rely on the country being in the
    string -- that's what the gazetteer is for."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Bangalore"),
        _owner_profile(),
    )
    assert passes is False, reason


def test_region_marked_not_remote_only_is_admitted_in_office():
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Dublin, Ireland"),
        _owner_profile(),
    )
    assert passes is True, reason


def test_no_geo_signal_at_all_falls_back_to_the_legacy_shim():
    """Documents the cost of LEGACY_REMOTE_ONLY_REGIONS honestly.

    This test previously asserted the opposite -- that a user with no geo
    config gets no location rejection at all, so a Mumbai-based user would
    keep their local market. The live corpus run (2026-09-28) overruled it:
    with no fallback, Rule 4 was disabled in production entirely and five
    India in-office roles reached the dashboard against explicit owner intent.

    So for the narrow case of "has queries but expresses no location
    preference whatsoever" the old single-tenant default still applies, and a
    Mumbai-based user in that state would still see their local market
    rejected. The scope is genuinely narrow -- load_config's fallback supplies
    locations, and Settings collects them, so tier 2 covers any real
    configured user (see test_policy_tier2_mumbai_user_keeps_their_own_market)
    -- but it is not zero, and it is the one place this refactor did not fully
    remove the owner's domain from the prefilter."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(queries=["Financial Controller"])
    assert profile.geo_policy_source == "legacy_fallback"
    passes, reason = merge_dedup._prefilter_job(
        _job("Financial Controller", _FINANCE_DESC, location="Mumbai, India"), profile
    )
    assert passes is False
    assert reason == "incompatible_location:india_in_office"


def test_mumbai_user_can_mark_ireland_remote_only_and_keep_local_jobs():
    """The mirror image of the owner's config, to prove the policy is really
    user-derived and not just the old list wearing a config hat."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Financial Controller"],
        geo_regions=[{"name": "India", "remote_only": False},
                     {"name": "Ireland", "remote_only": True}],
    )
    local, _ = merge_dedup._prefilter_job(
        _job("Financial Controller", _FINANCE_DESC, location="Mumbai, India"), profile
    )
    foreign, reason = merge_dedup._prefilter_job(
        _job("Financial Controller", _FINANCE_DESC, location="Dublin, Ireland"), profile
    )
    assert local is True
    assert foreign is False, reason


def test_us_in_office_still_admitted_for_the_owner_pending_followup():
    """Deliberate behaviour-preservation carve-out (owner decision,
    2026-09-28). The owner's geo_regions marks 'US (Remote)' remote_only, so
    enforcing remote_only for every region would newly reject in-office US
    jobs. Correct, but it lowers admit rate -- deferred until after the
    Oct 1 demo. GEO_REMOTE_ONLY_EXEMPT names the exempted region; clearing
    it to '' is the whole of the follow-up."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Austin, Texas"),
        _owner_profile(),
    )
    assert passes is True, reason


def test_geo_remote_only_exempt_none_rejects_us_in_office():
    """The follow-up's forward test: with the exemption cleared, the same job
    is rejected. Pins that the carve-out is scope-only, not a missing rule."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=_OWNER_QUERIES, experience_levels=_OWNER_EXPERIENCE_LEVELS,
        geo_regions=_OWNER_GEO_REGIONS, exempt_regions="",
    )
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Austin, Texas"), profile
    )
    assert passes is False, reason


# --- Rule 3 fails OPEN when the profile carries no signal -----------------
# Regression guard for a trap this refactor introduced and had to fix.
# Pre-2026-09-28, user_skills began as DEFAULT_USER_SKILLS for everyone, so a
# user whose user_search_configs row was missing or had empty `queries` still
# admitted tech jobs through Rule 3. Removing that IT default means such a
# profile has NO skills and NO phrases -- and Rule 3 then rejected 100% of
# the pool. On a live pipeline with a config row out of sync with config.yaml
# that is a total outage, not a tidy refactor.
#
# Skipping the rule is the honest response, and it matches how the rest of
# this module already handles missing inputs: Rule 0 passes a job with no
# posted_date, _region_for_location fails open on a city it can't resolve.
# With nothing to measure relevance against, "irrelevant" is not a finding.
# MAX_JOBS_PER_RUN still bounds the volume that reaches scoring.

def test_rule3_skipped_entirely_when_profile_has_no_signal():
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile()
    assert not profile.skills and not profile.query_phrases
    passes, reason = merge_dedup._prefilter_job(
        _job("Registered Nurse", _NURSING_DESC), profile
    )
    assert passes is True, f"an unconfigured user must not have everything rejected: {reason}"


def test_rule3_still_gates_once_the_profile_has_any_signal():
    """Fail-open must apply ONLY to a genuinely empty profile -- otherwise it
    would silently disable the relevance filter for everybody."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(queries=["ICU Registered Nurse"])
    passes, reason = merge_dedup._prefilter_job(
        _job("Financial Controller", _FINANCE_DESC), profile
    )
    assert passes is False
    assert reason.startswith("skill_overlap:")


def test_query_cancellation_matches_whole_words_not_substrings():
    """The cancellation check must not repeat the substring bug it exists to
    work around. "cto" is inside "doCTOr" and "inspeCTOr", so a naive
    substring test let a user searching "Doctor" cancel the exec-tier "cto"
    keyword and start seeing Chief Technology Officer postings."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Doctor", "Site Inspector"], experience_levels=["mid_level"],
    )
    assert profile.cancels("cto") is False
    assert profile.cancels("doctor") is True


def test_a_doctor_still_has_exec_titles_rejected():
    """End-to-end consequence of the above."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Doctor"], experience_levels=["mid_level"],
    )
    passes, reason = merge_dedup._prefilter_job(
        _job("Group CTO", _jd_mentioning("Doctor", _NURSING_DESC)), profile
    )
    assert passes is False
    assert reason == "role_mismatch:cto"


# --- Rule 4 policy source chain -------------------------------------------
# Caught by the live corpus run (13,677 rows, 2026-09-28), which my unit tests
# structurally could not catch: they built _OWNER_GEO_REGIONS by hand from
# config.yaml, so they verified an ASSUMPTION about the config shape rather
# than the shape production actually reads.
#
# `remote_only` is written nowhere but config.yaml -- not by the search-config
# API, not by Settings.jsx -- so user_search_configs.geo_regions never carries
# it. Deriving the policy from that field alone silently disabled Rule 4 in
# production: incompatible_location rejects went 6 -> 0 and five Hyderabad/
# Bangalore in-office roles were newly admitted, against explicit owner intent.
#
# So the policy now comes from the first source that actually expresses one:
#   1. geo_regions[].remote_only  -- explicit, per region (config.yaml today)
#   2. locations                  -- regions outside the user's target
#                                    locations are acceptable only remotely
#   3. LEGACY_REMOTE_ONLY_REGIONS -- single-tenant shim, documented, removable

def test_policy_tier1_explicit_remote_only_flags_win():
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Cloud Engineer"],
        geo_regions=[{"name": "India", "remote_only": True},
                     {"name": "Ireland", "remote_only": False}],
        locations=["Mumbai"],  # must NOT override an explicit flag
    )
    assert profile.remote_only_regions == frozenset({"india"})
    assert profile.geo_policy_source == "geo_regions.remote_only"


def test_policy_tier2_derives_from_target_locations():
    """The live-config case: geo_regions carries name/geo_id but no
    remote_only, so the target locations decide. Anything outside them is
    acceptable only remotely."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Cloud Engineer"],
        geo_regions=[{"name": "India", "geo_id": "102713980"}],
        locations=["Dublin, Ireland", "Remote"],
    )
    assert profile.geo_policy_source == "locations"
    assert "india" in profile.remote_only_regions
    assert "ireland" not in profile.remote_only_regions


def test_policy_tier2_mumbai_user_keeps_their_own_market():
    """The bug the shim must not re-create: a Mumbai-based user's local
    market stays in-office-acceptable, and Ireland becomes remote-only."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Financial Controller"], locations=["Mumbai, India"],
    )
    assert "india" not in profile.remote_only_regions
    assert "ireland" in profile.remote_only_regions
    local, _ = merge_dedup._prefilter_job(
        _job("Financial Controller", _jd_mentioning("Financial Controller", _FINANCE_DESC),
             location="Mumbai, India"), profile)
    assert local is True


def test_policy_tier3_legacy_shim_only_when_nothing_expresses_a_policy():
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(queries=["Cloud Engineer"])
    assert profile.geo_policy_source == "legacy_fallback"
    assert profile.remote_only_regions == merge_dedup.LEGACY_REMOTE_ONLY_REGIONS


def test_live_config_shape_still_rejects_india_in_office():
    """The exact regression the corpus run surfaced. geo_regions present but
    carrying no remote_only key, which is what production reads."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Site Reliability Engineer", "Python Developer"],
        experience_levels=["entry_level", "mid_level"],
        geo_regions=[{"name": "Ireland", "geo_id": "104738515"},
                     {"name": "India", "geo_id": "102713980"}],
        locations=["Dublin, Ireland"],
    )
    for loc in ("Hyderabad, India", "Bangalore", "Tokyo, Bangalore"):
        passes, reason = merge_dedup._prefilter_job(
            _job("Site Reliability Engineer", _GOOD_DESC, location=loc), profile)
        assert passes is False, f"{loc!r} newly admitted: this is the corpus regression"
        assert reason.startswith("incompatible_location:")


def test_live_config_shape_still_admits_us_in_office():
    """The owner's carve-out must survive the new policy chain."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Site Reliability Engineer", "Python Developer"],
        geo_regions=[{"name": "Ireland", "geo_id": "104738515"}],
        locations=["Dublin, Ireland"],
    )
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="Austin, Texas"), profile)
    assert passes is True, reason


# --- The live config row, verbatim ----------------------------------------
# Second corpus run (13,677 rows, 2026-09-28) printed the production row:
#   queries=['Site Reliability Engineer','DevOps Engineer','Full Stack Engineer',
#            'Backend Engineer']
#   experience_levels=['mid_level']  locations=['Dublin','Ireland']
#   geo_regions=None  include_internships=None
#
# Tier 2 therefore fires, and the FIRST version of it over-rejected badly:
# targets={ireland} made {india,uk,us} remote-only, and with only "us" exempt
# that rejected 107 jobs (-0.75pp), almost all of them London/Manchester/
# Edinburgh SRE and DevOps roles -- the owner's exact target roles.
#
# Root cause: `locations` expresses a PREFERENCE, not an exclusion. The old
# rule excluded exactly one region; every other foreign location was
# deliberately left to shared/work_auth.py, which CAPS non-home-country jobs
# to A-tier rather than rejecting them. Turning a preference list into an
# exclusion set contradicts that shipped design.
#
# The exemption now covers the regions the old rule tolerated in-office, so
# tier 2 stays genuinely derived from the user's own config while the owner's
# admit rate is unchanged.

_LIVE_CONFIG = dict(
    queries=["Site Reliability Engineer", "DevOps Engineer", "Full Stack Engineer",
             "Backend Engineer"],
    experience_levels=["mid_level"],
    locations=["Dublin", "Ireland"],
    geo_regions=None,
)


def _live_profile(**over):
    import merge_dedup
    return merge_dedup.build_prefilter_profile(**{**_LIVE_CONFIG, **over})


def test_live_config_enforces_india_only():
    import merge_dedup
    profile = _live_profile()
    enforced = {r for r in profile.remote_only_regions
                if merge_dedup._region_is_remote_only(r, profile)}
    assert enforced == {"india"}, f"over-rejecting: {enforced}"


@pytest.mark.parametrize("location", [
    "London, UK", "Manchester, Greater Manchester", "Edinburgh, Scotland",
    "Glasgow City Centre, Glasgow", "The City, Central London", "Shoreditch, London",
])
def test_live_config_still_admits_uk_in_office(location):
    """The 107 jobs the first tier-2 cut wrongly rejected. Real titles and
    locations taken from the corpus run's newly-rejected sample."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location=location), _live_profile())
    assert passes is True, f"{location!r} wrongly rejected: {reason}"


@pytest.mark.parametrize("location", ["Hyderabad, India", "Bangalore", "Tokyo, Bangalore"])
def test_live_config_still_rejects_india_in_office(location):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location=location), _live_profile())
    assert passes is False
    assert reason == "incompatible_location:india_in_office"


def test_live_config_admits_dublin_and_us_in_office():
    import merge_dedup
    for location in ("Dublin, County Dublin, Ireland", "Austin, Texas", "Los Angeles, CA"):
        passes, reason = merge_dedup._prefilter_job(
            _job("Site Reliability Engineer", _GOOD_DESC, location=location), _live_profile())
        assert passes is True, f"{location!r}: {reason}"


def test_mumbai_mirror_of_the_live_config_keeps_local_jobs():
    """Tier 2 is still genuinely per-user: the same code path gives a
    Mumbai-based user the mirror-image policy, with no special-casing."""
    import merge_dedup
    profile = merge_dedup.build_prefilter_profile(
        queries=["Financial Controller"], locations=["Mumbai", "India"])
    assert profile.geo_policy_source == "locations"
    local, _ = merge_dedup._prefilter_job(
        _job("Financial Controller", _jd_mentioning("Financial Controller", _FINANCE_DESC),
             location="Mumbai, India"), profile)
    foreign, reason = merge_dedup._prefilter_job(
        _job("Financial Controller", _jd_mentioning("Financial Controller", _FINANCE_DESC),
             location="Dublin, Ireland"), profile)
    assert local is True
    assert foreign is False, reason


def test_retiring_the_exemption_enforces_uk_too():
    """Forward test for the follow-up. Clearing GEO_REMOTE_ONLY_EXEMPT makes
    every region the user's config marks remote-only actually enforced --
    which is correct, and costs ~107 jobs for this user, which is why it is
    a deliberate follow-up rather than part of this change."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _job("Site Reliability Engineer", _GOOD_DESC, location="London, UK"),
        _live_profile(exempt_regions=""))
    assert passes is False
    assert reason == "incompatible_location:uk_in_office"
