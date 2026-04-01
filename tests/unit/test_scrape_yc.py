"""Unit tests for scrape_yc Lambda."""
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
        "query_hash": "ychash",
        "cache_ttl_hours": 48,
    }


# New WATS API response shape: props.jobs[] (flat list, Inertia JSON)
_WAS_JOBS_RESPONSE = {
    "props": {
        "jobs": [
            {
                "id": 101,
                "title": "Full Stack Engineer",
                "companyName": "YC Startup A",
                "companySlug": "yc-startup-a",
                "companyOneLiner": "Build web apps with React and Python.",
                "location": "Remote",
            },
            {
                "id": 202,
                "title": "ML Engineer",
                "companyName": "YC Startup B",
                "companySlug": "yc-startup-b",
                "companyOneLiner": "Work on cutting-edge ML models.",
                "location": "San Francisco",
            },
        ]
    }
}

# HTML page that contains the Inertia version
_WAS_HTML = '<div id="app" data-page="{&quot;component&quot;:&quot;Jobs&quot;,&quot;version&quot;:&quot;abc123&quot;}"></div>'


# ---------------------------------------------------------------------------
# happy_path
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_yc._get_inertia_version", return_value="abc123")
@patch("scrape_yc.get_param")
@patch("scrape_yc.get_supabase")
def test_happy_path(mock_get_supabase, mock_get_param, mock_version):
    """WATS /jobs Inertia response → jobs extracted and stored → {count, source}."""
    import scrape_yc

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    # Mock the Inertia JSON request for each query
    respx.get(url__regex=r".*workatastartup\.com/jobs.*").mock(
        return_value=httpx.Response(200, json=_WAS_JOBS_RESPONSE)
    )

    result = scrape_yc.handler(_base_event(), {})

    assert result["count"] == 2
    assert result["source"] == "yc"
    assert "cached" not in result


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
