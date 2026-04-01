"""Unit tests for scrape_hn Lambda."""
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
    return {"query_hash": "hnhash", "cache_ttl_hours": 168}


# Algolia thread search response
_THREAD_RESPONSE = {
    "hits": [{"objectID": "thread99"}]
}

# Two valid comments (>= 50 chars) plus one short one that should be filtered
_SHORT_COMMENT = "Too short"
_VALID_COMMENT_1 = (
    "Acme Corp | Software Engineer | Dublin, Ireland | REMOTE\n"
    "We are looking for a talented Python engineer to join our team and build scalable services."
)
_VALID_COMMENT_2 = (
    "BetaCo | Data Engineer | Cork, Ireland\n"
    "Join our data platform team and work with cutting-edge streaming technologies like Kafka."
)

_COMMENTS_RESPONSE = {
    "hits": [
        {"comment_text": _SHORT_COMMENT},
        {"comment_text": _VALID_COMMENT_1},
        {"comment_text": _VALID_COMMENT_2},
        {"comment_text": ""},  # empty — should be skipped
    ]
}


# ---------------------------------------------------------------------------
# happy_path
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_happy_path(mock_get_supabase, mock_get_param):
    """Algolia returns thread + valid comments → parsed jobs returned."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    # First request: thread search
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        side_effect=[
            httpx.Response(200, json=_THREAD_RESPONSE),
            httpx.Response(200, json=_COMMENTS_RESPONSE),
        ]
    )

    result = scrape_hn.handler(_base_event(), {})

    # Only the two valid, long-enough comments produce jobs
    assert result["count"] == 2
    assert result["source"] == "hn_hiring"
    assert "cached" not in result
    assert "error" not in result


# ---------------------------------------------------------------------------
# cache_hit
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param):
    """Recent rows in DB → returns cached=True without any HTTP calls."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=7)
    mock_get_param.return_value = "mock-value"

    result = scrape_hn.handler(_base_event(), {})

    assert result == {"count": 7, "source": "hn_hiring", "cached": True}
    assert respx.calls.call_count == 0


# ---------------------------------------------------------------------------
# parse_hn_comment — unit tests for the standalone function
# ---------------------------------------------------------------------------

def test_parse_hn_comment_pipe_separated():
    """Pipe-separated first line → company, title, location extracted."""
    import scrape_hn

    text = (
        "Acme Corp | Backend Engineer | Dublin, Ireland\n"
        "We are building amazing products and looking for talented people."
    )
    result = scrape_hn.parse_hn_comment(text)

    assert result is not None
    assert result["company"] == "Acme Corp"
    assert result["title"] == "Backend Engineer"
    assert result["location"] == "Dublin, Ireland"
    assert "Acme Corp" in result["description"]


def test_parse_hn_comment_short_returns_none():
    """Comment with only one pipe field (no title) → None."""
    import scrape_hn

    # Only company, no title after pipe
    result = scrape_hn.parse_hn_comment("JustACompany\nSome description here.")

    assert result is None


def test_parse_hn_comment_empty_returns_none():
    """Empty string → None."""
    import scrape_hn

    result = scrape_hn.parse_hn_comment("")

    assert result is None


def test_parse_hn_comment_html_entities_unescaped():
    """HTML entities in the comment text are unescaped."""
    import scrape_hn

    text = (
        "Acme &amp; Co | Engineer | Dublin\n"
        "Work with us on &lt;interesting&gt; problems every single day."
    )
    result = scrape_hn.parse_hn_comment(text)

    assert result is not None
    assert "&amp;" not in result["company"]
    assert "Acme & Co" == result["company"]
