"""Unit tests for scrape_apify Lambda."""
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(count=0, monthly_data=None):
    """Return a mock Supabase client pre-configured for scrape_apify."""
    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table

    # Every chained call on the table returns the same table mock so that
    # .select().eq().eq().gte().execute() works without extra setup.
    for method in ("select", "eq", "gte", "in_", "order", "limit",
                   "insert", "update", "upsert", "delete"):
        getattr(table, method).return_value = table

    execute_result = MagicMock()
    execute_result.count = count
    execute_result.data = monthly_data if monthly_data is not None else []
    table.execute.return_value = execute_result

    return db


def _base_event():
    return {
        "actor_id": "apify/web-scraper",
        "run_input": {"startUrls": [{"url": "https://example.com"}]},
        "source": "linkedin",
        "normalizer": "linkedin",
        "query_hash": "hash123",
        "cache_ttl_hours": 24,
    }


# ---------------------------------------------------------------------------
# happy_path
# ---------------------------------------------------------------------------

@patch("scrape_apify.ApifyClient")
@patch("scrape_apify.get_param")
@patch("scrape_apify.get_supabase")
def test_happy_path(mock_get_supabase, mock_get_param, mock_apify_cls):
    """Valid input: Apify runs, jobs normalised and stored → {count, source}."""
    import scrape_apify

    # DB: cache miss (count=0), no monthly spend
    mock_get_supabase.return_value = _make_db(count=0, monthly_data=[])
    mock_get_param.return_value = "mock-value"

    # Apify client: actor call returns two valid LinkedIn-shaped items
    fake_items = [
        {
            "title": "Software Engineer",
            "companyName": "Acme",
            "description": "Build great things",
            "location": "Dublin",
            "url": "https://linkedin.com/jobs/1",
        },
        {
            "title": "Data Engineer",
            "companyName": "BetaCo",
            "description": "Handle data pipelines",
            "location": "Cork",
            "url": "https://linkedin.com/jobs/2",
        },
    ]
    mock_run = {"defaultDatasetId": "ds-123"}
    mock_dataset = MagicMock()
    mock_dataset.list_items.return_value = MagicMock(items=fake_items)

    mock_client_instance = MagicMock()
    mock_client_instance.actor.return_value.call.return_value = mock_run
    mock_client_instance.dataset.return_value = mock_dataset
    mock_apify_cls.return_value = mock_client_instance

    result = scrape_apify.handler(_base_event(), {})

    assert result["count"] == 2
    assert result["source"] == "linkedin"
    assert "cached" not in result
    assert "error" not in result
    # Apify must have been called
    mock_client_instance.actor.assert_called_once_with("apify/web-scraper")


# ---------------------------------------------------------------------------
# cache_hit
# ---------------------------------------------------------------------------

@patch("scrape_apify.ApifyClient")
@patch("scrape_apify.get_param")
@patch("scrape_apify.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param, mock_apify_cls):
    """When DB already has recent rows, Apify must NOT be called."""
    import scrape_apify

    # DB: cache hit (count > 0)
    mock_get_supabase.return_value = _make_db(count=5)
    mock_get_param.return_value = "mock-value"

    result = scrape_apify.handler(_base_event(), {})

    assert result == {"count": 5, "source": "linkedin", "cached": True}
    mock_apify_cls.assert_not_called()


# ---------------------------------------------------------------------------
# budget_exceeded
# ---------------------------------------------------------------------------

@patch("scrape_apify.ApifyClient")
@patch("scrape_apify.get_param")
@patch("scrape_apify.get_supabase")
def test_budget_exceeded(mock_get_supabase, mock_get_param, mock_apify_cls, monkeypatch):
    """Apify usage >= hard limit → skipped: budget_exceeded, Apify NOT called."""
    import scrape_apify

    monkeypatch.setenv("APIFY_BUDGET_LIMIT_USD", "4.80")

    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table
    for method in ("select", "eq", "gte", "in_", "order", "limit",
                   "insert", "update", "upsert", "delete"):
        getattr(table, method).return_value = table

    cache_result = MagicMock()
    cache_result.count = 0
    table.execute.return_value = cache_result

    mock_get_supabase.return_value = db
    mock_get_param.return_value = "mock-value"

    # Mock _check_apify_budget to return usage above hard limit
    with patch.object(scrape_apify, '_check_apify_budget', return_value=(4.90, 5.0)):
        with patch.object(scrape_apify, '_send_budget_alert'):
            result = scrape_apify.handler(_base_event(), {})

    assert result["skipped"] == "budget_exceeded"
    assert result["count"] == 0
    mock_apify_cls.assert_not_called()


# ---------------------------------------------------------------------------
# actor_failure
# ---------------------------------------------------------------------------

@patch("scrape_apify.ApifyClient")
@patch("scrape_apify.get_param")
@patch("scrape_apify.get_supabase")
def test_actor_failure(mock_get_supabase, mock_get_param, mock_apify_cls):
    """ApifyClient raises an exception → returns {count: 0, error: '...'}."""
    import scrape_apify

    # DB: cache miss, no monthly spend
    mock_get_supabase.return_value = _make_db(count=0, monthly_data=[])
    mock_get_param.return_value = "mock-value"

    # Apify actor call blows up
    mock_client_instance = MagicMock()
    mock_client_instance.actor.return_value.call.side_effect = RuntimeError("timeout")
    mock_apify_cls.return_value = mock_client_instance

    result = scrape_apify.handler(_base_event(), {})

    assert result["count"] == 0
    assert result["source"] == "linkedin"
    assert "timeout" in result["error"]
