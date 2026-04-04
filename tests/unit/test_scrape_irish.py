"""Unit tests for scrape_irish Lambda — IrishJobs 403 fix.

Tests the two-tier detail page fetching strategy:
1. Direct request (works for Jobs.ie)
2. Web Unlocker proxy fallback (needed for IrishJobs 403s)
3. Graceful degradation when both fail (job saved with empty description)
"""
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
        "query_hash": "hash789",
        "cache_ttl_hours": 24,
    }


# Minimal StepStone search page HTML with one job card
_SEARCH_HTML = """
<html><body>
<div id="job-item-12345">
  <img data-testid="COMPANY_LOGO_IMAGE" alt="Acme Corp" />
  <h2><a href="/job/python-dev-12345" data-testid="job-title">Python Developer</a></h2>
</div>
</body></html>
"""

# Detail page HTML with a valid vacancy description (>100 chars)
_DETAIL_HTML_OK = """
<html><body>
<div data-testid="vacancy-description">
  We are looking for a talented Python Developer to join our team.
  You will work on exciting projects involving machine learning, data pipelines,
  and cloud infrastructure. Requirements include 3+ years of Python experience,
  familiarity with AWS services, and strong problem-solving skills.
</div></div>
</body></html>
"""

# Detail page HTML with too-short description (should not match)
_DETAIL_HTML_SHORT = """
<html><body>
<div data-testid="vacancy-description">Short desc</div></div>
</body></html>
"""


# ---------------------------------------------------------------------------
# _extract_description
# ---------------------------------------------------------------------------

def test_extract_description_vacancy():
    """Extracts description from data-testid='vacancy-description' div."""
    import scrape_irish
    desc = scrape_irish._extract_description(_DETAIL_HTML_OK)
    assert "Python Developer" in desc
    assert len(desc) > 100


def test_extract_description_too_short():
    """Returns empty string when description is under 100 chars."""
    import scrape_irish
    desc = scrape_irish._extract_description(_DETAIL_HTML_SHORT)
    assert desc == ""


def test_extract_description_no_match():
    """Returns empty string when no description div is found."""
    import scrape_irish
    desc = scrape_irish._extract_description("<html><body>No desc here</body></html>")
    assert desc == ""


# ---------------------------------------------------------------------------
# _fetch_detail_page — direct success
# ---------------------------------------------------------------------------

@respx.mock
def test_fetch_detail_direct_success():
    """Direct fetch returns 200 with valid description — no proxy needed."""
    import scrape_irish

    respx.get("https://www.irishjobs.ie/job/python-dev-12345").mock(
        return_value=httpx.Response(200, text=_DETAIL_HTML_OK)
    )

    desc, quality = scrape_irish._fetch_detail_page(
        "https://www.irishjobs.ie/job/python-dev-12345",
        "https://www.irishjobs.ie/jobs/software-engineer",
        proxy_url=None,
    )
    assert quality == "full"
    assert "Python Developer" in desc


# ---------------------------------------------------------------------------
# _fetch_detail_page — 403 then proxy success
# ---------------------------------------------------------------------------

@respx.mock
def test_fetch_detail_403_then_proxy():
    """Direct returns 403, proxy returns 200 with description."""
    import scrape_irish

    # Direct request returns 403
    respx.get("https://www.irishjobs.ie/job/python-dev-12345").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )

    # Proxy request succeeds — respx doesn't filter by proxy, so we use
    # side_effect to simulate the two-call pattern
    call_count = {"n": 0}
    original_get = httpx.get

    def mock_get(url, **kwargs):
        call_count["n"] += 1
        if "proxy" in kwargs and kwargs["proxy"]:
            return httpx.Response(200, text=_DETAIL_HTML_OK)
        return httpx.Response(403, text="Forbidden")

    with patch("scrape_irish.httpx.get", side_effect=mock_get):
        desc, quality = scrape_irish._fetch_detail_page(
            "https://www.irishjobs.ie/job/python-dev-12345",
            "https://www.irishjobs.ie/jobs/software-engineer",
            proxy_url="http://proxy.example.com:1234",
        )

    assert quality == "full"
    assert "Python Developer" in desc
    assert call_count["n"] == 2  # direct + proxy


# ---------------------------------------------------------------------------
# _fetch_detail_page — both fail gracefully
# ---------------------------------------------------------------------------

@respx.mock
def test_fetch_detail_both_fail():
    """Both direct and proxy fail — returns empty description with quality 'none'."""
    import scrape_irish

    def mock_get(url, **kwargs):
        return httpx.Response(403, text="Forbidden")

    with patch("scrape_irish.httpx.get", side_effect=mock_get):
        desc, quality = scrape_irish._fetch_detail_page(
            "https://www.irishjobs.ie/job/python-dev-12345",
            "https://www.irishjobs.ie/jobs/software-engineer",
            proxy_url="http://proxy.example.com:1234",
        )

    assert quality == "none"
    assert desc == ""


# ---------------------------------------------------------------------------
# _fetch_detail_page — exception handling
# ---------------------------------------------------------------------------

def test_fetch_detail_exception_handling():
    """Network errors are caught, job is saved with empty description."""
    import scrape_irish

    def mock_get(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch("scrape_irish.httpx.get", side_effect=mock_get):
        desc, quality = scrape_irish._fetch_detail_page(
            "https://www.irishjobs.ie/job/python-dev-12345",
            "https://www.irishjobs.ie/jobs/software-engineer",
            proxy_url="http://proxy.example.com:1234",
        )

    assert quality == "none"
    assert desc == ""


# ---------------------------------------------------------------------------
# _fetch_detail_page — no proxy available
# ---------------------------------------------------------------------------

def test_fetch_detail_no_proxy_on_403():
    """Direct returns 403 and no proxy configured — returns empty."""
    import scrape_irish

    def mock_get(url, **kwargs):
        return httpx.Response(403, text="Forbidden")

    with patch("scrape_irish.httpx.get", side_effect=mock_get):
        desc, quality = scrape_irish._fetch_detail_page(
            "https://www.irishjobs.ie/job/python-dev-12345",
            "https://www.irishjobs.ie/jobs/software-engineer",
            proxy_url=None,
        )

    assert quality == "none"
    assert desc == ""


# ---------------------------------------------------------------------------
# _fetch_detail_page — Referer header is set
# ---------------------------------------------------------------------------

def test_fetch_detail_sends_referer():
    """Detail page request includes Referer header set to search URL."""
    import scrape_irish

    captured_headers = {}

    def mock_get(url, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return httpx.Response(200, text=_DETAIL_HTML_OK)

    with patch("scrape_irish.httpx.get", side_effect=mock_get):
        scrape_irish._fetch_detail_page(
            "https://www.irishjobs.ie/job/python-dev-12345",
            "https://www.irishjobs.ie/jobs/software-engineer",
            proxy_url=None,
        )

    assert captured_headers.get("Referer") == "https://www.irishjobs.ie/jobs/software-engineer"


# ---------------------------------------------------------------------------
# handler — cache hit
# ---------------------------------------------------------------------------

@patch("scrape_irish.get_param")
@patch("scrape_irish.get_supabase")
def test_cache_hit(mock_get_supabase, mock_get_param):
    """Recent rows in DB -> cached=True, no HTTP calls."""
    import scrape_irish

    mock_get_supabase.return_value = _make_db(count=10)
    mock_get_param.return_value = "mock-value"

    result = scrape_irish.handler(_base_event(), {})

    assert result == {"count": 10, "source": "irish_portals", "cached": True}


# ---------------------------------------------------------------------------
# handler — proxy_url fetch failure is non-fatal
# ---------------------------------------------------------------------------

@patch("scrape_irish.get_param")
@patch("scrape_irish.get_supabase")
def test_proxy_url_fetch_failure_non_fatal(mock_get_supabase, mock_get_param):
    """If PROXY_URL SSM param is missing, scraper still runs (without proxy)."""
    import scrape_irish

    mock_get_supabase.return_value = _make_db(count=0)

    def param_side_effect(name):
        if name == "/naukribaba/PROXY_URL":
            raise Exception("ParameterNotFound")
        return "mock-value"

    mock_get_param.side_effect = param_side_effect

    # Mock httpx.Client to return empty search results
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body>No jobs</body></html>"
    mock_client.get.return_value = mock_response

    with patch("scrape_irish.httpx.Client", return_value=mock_client):
        result = scrape_irish.handler(_base_event(), {})

    # Should not crash — returns 0 jobs
    assert result["count"] == 0
    assert result["source"] == "irish_portals"


# ---------------------------------------------------------------------------
# _scrape_stepstone_site — integration with _fetch_detail_page
# ---------------------------------------------------------------------------

@patch("scrape_irish._fetch_detail_page")
def test_scrape_stepstone_passes_proxy(mock_fetch_detail):
    """_scrape_stepstone_site passes proxy_url down to _fetch_detail_page."""
    import scrape_irish

    mock_fetch_detail.return_value = ("Great job description here", "full")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _SEARCH_HTML
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_stepstone_site(
        "irishjobs", "https://www.irishjobs.ie",
        ["software engineer"], mock_client,
        proxy_url="http://proxy.example.com:1234",
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Developer"
    assert jobs[0]["company"] == "Acme Corp"
    assert jobs[0]["description"] == "Great job description here"
    assert jobs[0]["source"] == "irishjobs"

    # Verify proxy was passed through
    mock_fetch_detail.assert_called_once()
    call_args = mock_fetch_detail.call_args
    assert call_args[0][2] == "http://proxy.example.com:1234"  # proxy_url arg


# ---------------------------------------------------------------------------
# _scrape_stepstone_site — jobs saved even with empty descriptions
# ---------------------------------------------------------------------------

@patch("scrape_irish._fetch_detail_page")
def test_scrape_stepstone_empty_desc_still_saved(mock_fetch_detail):
    """Jobs with empty descriptions (403 on detail) are still returned."""
    import scrape_irish

    mock_fetch_detail.return_value = ("", "none")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _SEARCH_HTML
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_stepstone_site(
        "irishjobs", "https://www.irishjobs.ie",
        ["software engineer"], mock_client,
        proxy_url=None,
    )

    assert len(jobs) == 1
    assert jobs[0]["description"] == ""
    # Job is still saved with title, company, source
    assert jobs[0]["title"] == "Python Developer"
    assert jobs[0]["company"] == "Acme Corp"


# ===========================================================================
# GradIreland-specific tests (multi-strategy resilience)
# ===========================================================================

# JSON-LD page
_GRADIRELAND_JSON_LD = """
<html><head>
<script type="application/ld+json">
[
  {
    "@type": "JobPosting",
    "title": "Graduate Python Developer",
    "hiringOrganization": {"@type": "Organization", "name": "TechCorp"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Dublin"}},
    "url": "https://gradireland.com/graduate-jobs/techcorp/grad-python-dev",
    "description": "Exciting graduate role for Python developers."
  }
]
</script>
</head><body></body></html>
"""

# Drupal field--name-title page (original pattern)
_GRADIRELAND_DRUPAL_FIELDS = """
<html><body>
<h2 class="field--name-title"><a href="/graduate-jobs/megacorp/frontend-dev">Frontend Developer</a></h2>
<div class="field--name-field-company">MegaCorp</div>
<div class="field--name-field-location">Dublin</div>
</body></html>
"""

# Drupal views-row page
_GRADIRELAND_VIEWS_ROW = """
<html><body>
<div class="views-row views-row-1">
  <div class="views-field views-field-title">
    <span class="field-content"><a href="/graduate-jobs/acme/backend-dev">Backend Developer</a></span>
  </div>
  <div class="views-field views-field-field-company">
    <span class="field-content">Acme Corp</span>
  </div>
</div>
</body></html>
"""

# Empty page
_GRADIRELAND_EMPTY = "<html><body><p>No jobs found</p></body></html>"


# ---------------------------------------------------------------------------
# _scrape_gradireland — JSON-LD strategy
# ---------------------------------------------------------------------------

def test_scrape_gradireland_json_ld():
    """GradIreland scraper picks up JSON-LD jobs."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _GRADIRELAND_JSON_LD
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["python developer"], mock_client)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Graduate Python Developer"
    assert jobs[0]["company"] == "TechCorp"
    assert jobs[0]["source"] == "gradireland"


# ---------------------------------------------------------------------------
# _scrape_gradireland — Drupal field pattern (original)
# ---------------------------------------------------------------------------

def test_scrape_gradireland_drupal_fields():
    """GradIreland scraper falls back to Drupal field--name-title pattern."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _GRADIRELAND_DRUPAL_FIELDS
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["frontend developer"], mock_client)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Frontend Developer"
    assert jobs[0]["company"] == "MegaCorp"


# ---------------------------------------------------------------------------
# _scrape_gradireland — views-row strategy
# ---------------------------------------------------------------------------

def test_scrape_gradireland_views_row():
    """GradIreland scraper picks up Drupal views-row jobs."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _GRADIRELAND_VIEWS_ROW
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["backend developer"], mock_client)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Backend Developer"
    assert jobs[0]["company"] == "Acme Corp"


# ---------------------------------------------------------------------------
# _scrape_gradireland — URL path fallback
# ---------------------------------------------------------------------------

def test_scrape_gradireland_path_fallback():
    """If primary path returns 404, tries alternate paths."""
    import scrape_irish

    call_urls = []

    mock_client = MagicMock()

    def mock_get(url, **kwargs):
        call_urls.append(url)
        resp = MagicMock()
        if "/graduate-jobs" in url:
            resp.status_code = 404
            resp.text = ""
        elif "/jobs" in url:
            resp.status_code = 200
            resp.text = _GRADIRELAND_JSON_LD
        else:
            resp.status_code = 404
            resp.text = ""
        return resp

    mock_client.get.side_effect = mock_get

    jobs = scrape_irish._scrape_gradireland(["software engineer"], mock_client)

    # Should have tried /graduate-jobs first, then /jobs
    assert any("/graduate-jobs" in u for u in call_urls)
    assert any("/jobs" in u for u in call_urls)
    assert len(jobs) == 1


# ---------------------------------------------------------------------------
# _scrape_gradireland — zero jobs returns empty, no crash
# ---------------------------------------------------------------------------

def test_scrape_gradireland_zero_jobs():
    """Empty results return [] with no exceptions."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = _GRADIRELAND_EMPTY
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["data scientist"], mock_client)
    assert jobs == []


# ---------------------------------------------------------------------------
# _scrape_gradireland — bot protection handled gracefully
# ---------------------------------------------------------------------------

def test_scrape_gradireland_bot_protection():
    """Bot protection page stops scraping but does not crash."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body>Access Denied - Bot Protection</body></html>"
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["devops"], mock_client)
    assert jobs == []


# ---------------------------------------------------------------------------
# _scrape_gradireland — HTTP error handled gracefully
# ---------------------------------------------------------------------------

def test_scrape_gradireland_http_error():
    """Non-200/403/404 HTTP status does not crash."""
    import scrape_irish

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_client.get.return_value = mock_response

    jobs = scrape_irish._scrape_gradireland(["sre"], mock_client)
    assert jobs == []
