"""Unit tests for merge_dedup Lambda."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock


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
