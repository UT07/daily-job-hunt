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


# Algolia thread search response (search_by_date endpoint)
_THREAD_RESPONSE = {
    "hits": [
        {"objectID": "thread99", "title": "Ask HN: Who is hiring? (April 2026)"},
    ]
}

# Response with non-matching titles first, valid thread second
_THREAD_RESPONSE_WITH_NOISE = {
    "hits": [
        {"objectID": "noise1", "title": "Tell HN: Who Is Hiring Since 2016, Trend is evolving"},
        {"objectID": "thread99", "title": "Ask HN: Who is hiring? (April 2026)"},
        {"objectID": "noise2", "title": "Show HN: HN Jobs Trends"},
    ]
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
    """Algolia returns thread + valid comments -> parsed jobs returned."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    # Thread search uses search_by_date; comment fetch uses search
    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=_THREAD_RESPONSE)
    )
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=_COMMENTS_RESPONSE)
    )

    result = scrape_hn.handler(_base_event(), {})

    # Only the two valid, long-enough comments produce jobs
    assert result["count"] == 2
    assert result["source"] == "hn_hiring"
    assert "cached" not in result
    assert "error" not in result


# ---------------------------------------------------------------------------
# title_filtering — skips noise hits, picks real thread
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_title_filtering(mock_get_supabase, mock_get_param):
    """Non-matching titles are skipped; real 'Who is hiring?' thread is used."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=_THREAD_RESPONSE_WITH_NOISE)
    )
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=_COMMENTS_RESPONSE)
    )

    result = scrape_hn.handler(_base_event(), {})

    assert result["count"] == 2
    assert result["source"] == "hn_hiring"
    assert "error" not in result


# ---------------------------------------------------------------------------
# no_matching_thread — all hits are noise
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_no_matching_thread(mock_get_supabase, mock_get_param):
    """When no hit title matches the expected pattern, return error."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    noise_only = {
        "hits": [
            {"objectID": "x1", "title": "Show HN: Who Is Hiring Trends"},
            {"objectID": "x2", "title": "Tell HN: hiring is broken"},
        ]
    }
    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=noise_only)
    )

    result = scrape_hn.handler(_base_event(), {})

    assert result["count"] == 0
    assert result["error"] == "no_thread_found"


# ---------------------------------------------------------------------------
# cache_hit
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param):
    """Recent rows in DB -> returns cached=True without any HTTP calls."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=7)
    mock_get_param.return_value = "mock-value"

    result = scrape_hn.handler(_base_event(), {})

    assert result == {"count": 7, "source": "hn_hiring", "cached": True}
    assert respx.calls.call_count == 0


# ---------------------------------------------------------------------------
# comment_pagination — fetches multiple pages
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_hn.get_param")
@patch("scrape_hn.get_supabase")
def test_comment_pagination(mock_get_supabase, mock_get_param):
    """When first page is full (200 comments), fetches page 2."""
    import scrape_hn

    mock_get_supabase.return_value = _make_db(count=0)
    mock_get_param.return_value = "mock-value"

    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=_THREAD_RESPONSE)
    )

    # Page 0: 200 comments (full page triggers pagination)
    full_page = {"hits": [{"comment_text": _VALID_COMMENT_1}] * 200}
    # Page 1: partial page (stops pagination)
    partial_page = {"hits": [{"comment_text": _VALID_COMMENT_2}] * 50}

    call_count = {"n": 0}
    original_mock = respx.get("https://hn.algolia.com/api/v1/search")

    def _page_router(request):
        page = int(request.url.params.get("page", "0"))
        if page == 0:
            return httpx.Response(200, json=full_page)
        return httpx.Response(200, json=partial_page)

    original_mock.mock(side_effect=_page_router)

    result = scrape_hn.handler(_base_event(), {})

    # All 250 valid comments parsed (200 from page 0 + 50 from page 1),
    # but they're duplicates so dedup reduces to 2 unique
    assert result["source"] == "hn_hiring"
    assert "error" not in result
    # At least 2 pages of comments fetched (search_by_date + 2x search)
    assert respx.calls.call_count >= 3


# ---------------------------------------------------------------------------
# parse_hn_comment — unit tests for the standalone function
# ---------------------------------------------------------------------------

def test_parse_hn_comment_pipe_separated():
    """Pipe-separated first line -> company, title, location extracted."""
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
    """Comment with only one pipe field (no title) -> None."""
    import scrape_hn

    # Only company, no title after pipe
    result = scrape_hn.parse_hn_comment("JustACompany\nSome description here.")

    assert result is None


def test_parse_hn_comment_empty_returns_none():
    """Empty string -> None."""
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


# ---------------------------------------------------------------------------
# _find_latest_thread — unit tests
# ---------------------------------------------------------------------------

@respx.mock
def test_find_latest_thread_picks_correct_title():
    """_find_latest_thread skips noise and returns the correct objectID."""
    import scrape_hn

    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=_THREAD_RESPONSE_WITH_NOISE)
    )

    result = scrape_hn._find_latest_thread()
    assert result == "thread99"


@respx.mock
def test_find_latest_thread_returns_none_on_http_error():
    """HTTP error from Algolia -> returns None."""
    import scrape_hn

    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    result = scrape_hn._find_latest_thread()
    assert result is None


@respx.mock
def test_find_latest_thread_returns_none_when_no_hits():
    """Empty hits -> returns None."""
    import scrape_hn

    respx.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json={"hits": []})
    )

    result = scrape_hn._find_latest_thread()
    assert result is None


# ---------------------------------------------------------------------------
# apply-URL extraction (regression for hn_hiring 17%-coverage bug)
# ---------------------------------------------------------------------------

class TestExtractApplyUrl:
    def test_apply_prefix_takes_priority_over_random_url(self):
        from scrape_hn import extract_apply_url
        text = (
            "Acme | SRE | Dublin\n"
            "We're great. https://acme.com is our marketing site.\n"
            "Apply: https://acme.com/jobs/sre-2026"
        )
        # The "Apply:" prefix wins even though a generic acme.com URL appears
        # earlier in the body.
        assert extract_apply_url(text) == "https://acme.com/jobs/sre-2026"

    def test_url_path_hint_used_when_no_prefix(self):
        from scrape_hn import extract_apply_url
        text = (
            "Acme | SRE | Dublin\n"
            "Visit https://blog.acme.com for our blog\n"
            "https://acme.com/careers/sre to learn more about us"
        )
        assert extract_apply_url(text) == "https://acme.com/careers/sre"

    def test_email_fallback_when_no_url(self):
        from scrape_hn import extract_apply_url
        text = "Tiny Co | Founding Engineer | Remote\nEmail me: founder@tiny.co"
        assert extract_apply_url(text) == "mailto:founder@tiny.co"

    def test_first_url_when_nothing_better(self):
        from scrape_hn import extract_apply_url
        text = "Some company. https://www.example.com — that's all"
        assert extract_apply_url(text) == "https://www.example.com"

    def test_returns_empty_when_no_signal(self):
        from scrape_hn import extract_apply_url
        assert extract_apply_url("plain text with no url or email") == ""
        assert extract_apply_url("") == ""

    def test_handles_trailing_punctuation(self):
        from scrape_hn import extract_apply_url
        text = "Apply at https://acme.com/jobs."
        # The trailing period should NOT be part of the URL
        assert extract_apply_url(text) == "https://acme.com/jobs"

    def test_picks_workable_or_greenhouse_paths(self):
        from scrape_hn import extract_apply_url
        text = "Acme | Engineer\nhttps://www.workable.com/jobs/abc-co/eng-12345"
        assert "workable.com" in extract_apply_url(text)


class TestParseHnCommentApplyUrl:
    def test_href_anchor_preferred_over_text_url(self):
        """HN comments render apply links as <a href="...">. The href is the
        canonical destination; the text often shows a shortened version."""
        from scrape_hn import parse_hn_comment
        text = (
            "<p>Acme | Senior SRE | Dublin\n"
            "Join us. <a href=\"https://acme.com/careers/sre-2026\" rel=\"nofollow\">acme.com/careers/sre</a></p>"
        )
        job = parse_hn_comment(text)
        assert job is not None
        assert job["url"] == "https://acme.com/careers/sre-2026"

    def test_falls_back_to_comment_url_when_nothing_extractable(self):
        from scrape_hn import parse_hn_comment
        text = (
            "Acme | Engineer | Dublin\n"
            "We are hiring. No URL or email at all in this body."
        )
        job = parse_hn_comment(text, comment_url="https://news.ycombinator.com/item?id=42")
        assert job is not None
        assert job["url"] == "https://news.ycombinator.com/item?id=42"

    def test_apply_url_field_populated_when_email_only(self):
        from scrape_hn import parse_hn_comment
        text = (
            "Tiny Co | Founding Engineer | Remote\n"
            "Two-person team. Email founder@tinyco.io to apply."
        )
        job = parse_hn_comment(text, comment_url="https://news.ycombinator.com/item?id=42")
        assert job is not None
        # Email beats the comment-permalink fallback
        assert job["url"] == "mailto:founder@tinyco.io"

    def test_no_url_or_comment_url_returns_empty_string(self):
        from scrape_hn import parse_hn_comment
        text = "Acme | Engineer | Dublin\nNo links anywhere."
        job = parse_hn_comment(text)
        assert job is not None
        assert job["url"] == ""
