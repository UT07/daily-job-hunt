"""Unit tests for merge_dedup Lambda."""
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
