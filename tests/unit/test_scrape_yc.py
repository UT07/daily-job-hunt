"""Unit tests for scrape_yc Lambda."""
from unittest.mock import MagicMock, patch

import httpx
import pytest
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
        "query_hash": "ychash",
        "cache_ttl_hours": 48,
    }


# WorkAtAStartup JSON response shape: props.companies[].jobs[]
_WAS_RESPONSE = {
    "props": {
        "companies": [
            {
                "name": "YC Startup A",
                "jobs": [
                    {
                        "id": 101,
                        "title": "Full Stack Engineer",
                        "description": "Build web apps with React and Python.",
                        "location": "Remote",
                    }
                ],
            },
            {
                "name": "YC Startup B",
                "jobs": [
                    {
                        "id": 202,
                        "title": "ML Engineer",
                        "description": "Work on cutting-edge ML models.",
                        "location": "San Francisco",
                    }
                ],
            },
        ]
    }
}


# ---------------------------------------------------------------------------
# happy_path
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_yc.get_param")
@patch("scrape_yc.get_supabase")
def test_happy_path(mock_get_supabase, mock_get_param):
    """WorkAtAStartup JSON response → jobs extracted and stored → {count, source}."""
    import scrape_yc

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    respx.get("https://www.workatastartup.com/companies").mock(
        return_value=httpx.Response(200, json=_WAS_RESPONSE)
    )

    result = scrape_yc.handler(_base_event(), {})

    assert result["count"] == 2
    assert result["source"] == "yc"
    assert "cached" not in result
    assert "error" not in result


# ---------------------------------------------------------------------------
# cache_hit
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_yc.get_param")
@patch("scrape_yc.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param):
    """Recent rows in DB → returns cached=True without any HTTP calls."""
    import scrape_yc

    mock_get_supabase.return_value = _make_db(count=3)
    mock_get_param.return_value = "mock-value"

    result = scrape_yc.handler(_base_event(), {})

    assert result == {"count": 3, "source": "yc", "cached": True}
    assert respx.calls.call_count == 0
