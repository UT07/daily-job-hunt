"""Unit tests for scrape_adzuna Lambda."""
from unittest.mock import MagicMock, patch

import httpx
import respx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(count=0):
    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table
    for method in ("select", "eq", "gte", "in_", "order", "limit",
                   "insert", "update", "upsert", "delete"):
        getattr(table, method).return_value = table
    execute_result = MagicMock()
    execute_result.count = count
    execute_result.data = []
    table.execute.return_value = execute_result
    return db


def _base_event():
    return {
        "queries": ["software engineer"],
        "query_hash": "hash456",
        "cache_ttl_hours": 24,
    }


_ADZUNA_RESULTS = [
    {
        "title": "Python Developer",
        "company": {"display_name": "Acme Ltd"},
        "description": "Build Python services",
        "location": {"display_name": "Dublin"},
        "redirect_url": "https://www.adzuna.ie/jobs/123",
    },
    {
        "title": "Backend Engineer",
        "company": {"display_name": "BetaCorp"},
        "description": "Build backend APIs",
        "location": {"display_name": "Cork"},
        "redirect_url": "https://www.adzuna.ie/jobs/456",
    },
]


# ---------------------------------------------------------------------------
# happy_path
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_adzuna.get_param")
@patch("scrape_adzuna.get_supabase")
def test_happy_path(mock_get_supabase, mock_get_param):
    """API returns jobs → normalised and stored → {count, source}."""
    import scrape_adzuna

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    respx.get("https://api.adzuna.com/v1/api/jobs/gb/search/1").mock(
        return_value=httpx.Response(200, json={"results": _ADZUNA_RESULTS})
    )

    result = scrape_adzuna.handler(_base_event(), {})

    assert result["count"] == 2
    assert result["source"] == "adzuna"
    assert "cached" not in result


# ---------------------------------------------------------------------------
# cache_hit
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_adzuna.get_param")
@patch("scrape_adzuna.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param):
    """Recent rows in DB → returns cached=True, no HTTP call made."""
    import scrape_adzuna

    mock_get_supabase.return_value = _make_db(count=10)
    mock_get_param.return_value = "mock-value"

    # No respx route registered — any HTTP call would raise an error
    result = scrape_adzuna.handler(_base_event(), {})

    assert result == {"count": 10, "source": "adzuna", "cached": True}
    # Confirm no outbound requests were attempted
    assert respx.calls.call_count == 0


# ---------------------------------------------------------------------------
# api_error
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_adzuna.get_param")
@patch("scrape_adzuna.get_supabase")
def test_api_error(mock_get_supabase, mock_get_param):
    """HTTP 500 from Adzuna → query is skipped → {count: 0, source: 'adzuna'}."""
    import scrape_adzuna

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    respx.get("https://api.adzuna.com/v1/api/jobs/gb/search/1").mock(
        return_value=httpx.Response(500)
    )

    result = scrape_adzuna.handler(_base_event(), {})

    assert result["count"] == 0
    assert result["source"] == "adzuna"
    # No "error" key — the Lambda treats HTTP errors as soft failures per query
    assert "error" not in result
