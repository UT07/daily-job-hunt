"""Unit tests for merge_dedup Lambda."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest


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
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

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
    passes, reason = merge_dedup._prefilter_job(job, user_skills)
    assert passes is True
    assert reason == "pass"


def test_prefilter_rejects_stale_job():
    import merge_dedup
    # Default max_age_days = 14
    job = _fresh_job_with_posted(days_ago=30)
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, user_skills)
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
    passes, reason = merge_dedup._prefilter_job(job, user_skills)
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
    passes, reason = merge_dedup._prefilter_job(job, {"python"})
    assert passes is False
    assert reason.startswith("stale:")


def test_prefilter_custom_max_age():
    """Stricter age threshold rejects less-stale jobs."""
    import merge_dedup
    job = _fresh_job_with_posted(days_ago=10)
    user_skills = {"python", "aws", "kubernetes"}
    passes, reason = merge_dedup._prefilter_job(job, user_skills, max_age_days=7)
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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
    assert passes is False
    assert reason == "role_mismatch:manager"


@pytest.mark.parametrize("title", [
    "Solutions Architect, Enterprise",
    "Manager, Solutions Architects",  # plural form
    "Principal Solutions Architect",
])
def test_prefilter_rejects_architect_titles(title):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
    assert passes is False


@pytest.mark.parametrize("title,expected_kw", [
    ("Senior Data Scientist", "data scientist"),
    ("Senior Developer Advocate", "developer advocate"),
    ("Recruiter", "recruiter"),
    ("Compensation Analyst", "compensation analyst"),
    ("Controller", "controller"),
    ("Senior Revenue Analytics Analyst", "revenue analytics"),
    ("2026 - Women in Tech Summit, EMEA", "summit"),
])
def test_prefilter_rejects_off_archetype_titles(title, expected_kw):
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
    assert passes is False
    assert reason == f"role_mismatch:{expected_kw}"


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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), {"python", "aws"})
    assert passes is True, f"{title!r} was wrongly rejected: {reason}"


def test_prefilter_does_not_reject_salesforce_titles():
    """Regression guard: 'sales' is a substring of 'Salesforce', a real
    platform-engineering title observed in the corpus (Ashby/Benchling:
    'Salesforce Engineer, Business Technology Team'). This is why
    REJECT_TITLE_KEYWORDS uses scoped phrases like 'sales engineer' instead
    of a bare 'sales' keyword."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _good_job("Salesforce Engineer, Business Technology Team"), {"python", "aws"}
    )
    assert passes is True, f"wrongly rejected: {reason}"


def test_prefilter_does_not_reject_security_partnerships_title():
    """Regression guard: 'partner' is not a bare reject keyword because it
    matched 'Staff Security Engineer, Security Partnerships' — a real IC
    security role — in the live corpus scan."""
    import merge_dedup
    passes, reason = merge_dedup._prefilter_job(
        _good_job("Staff Security Engineer, Security Partnerships"), {"python", "aws"}
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
    passes, reason = merge_dedup._prefilter_job(_good_job(title), merge_dedup.DEFAULT_USER_SKILLS)
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
    db = _make_supabase(jobs_raw_data=jobs_raw, existing_jobs_data=[])

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
