"""Pins the ATS board lists the Ashby and Greenhouse scrapers fall back to,
and the visibility of a board that stops answering.

Background — audited 2026-09-28. `DEFAULT_COMPANIES` in scrape_ashby.py is
reached only when `/naukribaba/ASHBY_COMPANIES` is unreadable, so nothing ever
exercised it and it rotted unnoticed:

    anthropic  404        figma   404
    linear     200,  30   retool  404
    vercel     200,   0
    notion     200, 129

An SSM outage would have dropped the scraper to 2 working boards out of 6 and
reported success — `count` is still non-zero, and self_improve only flags a
scraper after 3 consecutive days of ZERO jobs, which a half-working list never
reaches. The same audit found all ten Greenhouse defaults healthy.

Two kinds of check live here:

* The offline tests run in CI. They pin the lists (so an edit has to state its
  own verification) and cover the log/return-value signals a dead board now
  raises.
* The live tests below are opt-in and skipped by default — CI must not depend
  on a third-party API. They are the actual re-verification step:

      NAUKRIBABA_LIVE_BOARD_CHECK=1 pytest tests/unit/test_scraper_default_boards.py
"""
import json
import logging
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

LIVE_CHECK = os.environ.get("NAUKRIBABA_LIVE_BOARD_CHECK") == "1"
live_only = pytest.mark.skipif(
    not LIVE_CHECK,
    reason="hits the live Ashby/Greenhouse APIs; set NAUKRIBABA_LIVE_BOARD_CHECK=1 to run",
)

# Verified 404 on 2026-09-28. Kept by name so a revert or a copy-paste from an
# old branch fails loudly instead of quietly costing boards again.
ASHBY_SLUGS_VERIFIED_DEAD = {"anthropic", "figma", "retool"}


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


def _ashby_board(n_jobs=1):
    return {
        "organizationName": "Test Org",
        "jobs": [
            {
                "id": f"job-{i}",
                "title": f"Backend Engineer {i}",
                "location": "Dublin, Ireland",
                "isRemote": False,
                "descriptionHtml": "<p>Build things.</p>",
                "jobUrl": f"https://jobs.ashbyhq.com/test/job-{i}",
                "publishedAt": "2026-09-27T00:00:00Z",
            }
            for i in range(n_jobs)
        ],
    }


def _greenhouse_board(n_jobs=1):
    return {
        "jobs": [
            {
                "id": 1000 + i,
                "title": f"Backend Engineer {i}",
                "location": {"name": "Dublin, Ireland"},
                "content": "<p>Build things.</p>",
                "absolute_url": f"https://boards.greenhouse.io/test/jobs/{1000 + i}",
                "updated_at": "2026-09-27T00:00:00Z",
            }
            for i in range(n_jobs)
        ]
    }


def _errors(caplog):
    return [r.message for r in caplog.records if r.levelno >= logging.ERROR]


def _warnings(caplog):
    return [r.message for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# The lists themselves
# ---------------------------------------------------------------------------

def test_ashby_defaults_are_the_verified_set():
    """Changing this list means re-running the live check above and updating
    both the list and the audit date in scrape_ashby.py."""
    import scrape_ashby

    assert scrape_ashby.DEFAULT_COMPANIES == [
        "linear", "notion", "ramp", "benchling", "abridge", "livekit",
    ]


def test_ashby_defaults_exclude_boards_verified_dead():
    import scrape_ashby

    dead = ASHBY_SLUGS_VERIFIED_DEAD.intersection(scrape_ashby.DEFAULT_COMPANIES)
    assert not dead, f"these slugs returned 404 on 2026-09-28: {sorted(dead)}"


def test_greenhouse_defaults_are_the_verified_set():
    """All ten answered 200 with jobs on 2026-09-28 — unchanged by that audit."""
    import scrape_greenhouse

    assert scrape_greenhouse.DEFAULT_BOARDS == [
        "stripe", "intercom", "mongodb", "twilio", "datadog",
        "pagerduty", "toast", "cloudflare", "elastic", "ripple",
    ]


@pytest.mark.parametrize("module_name, attr", [
    ("scrape_ashby", "DEFAULT_COMPANIES"),
    ("scrape_greenhouse", "DEFAULT_BOARDS"),
])
def test_default_lists_have_no_duplicates(module_name, attr):
    module = __import__(module_name)
    slugs = getattr(module, attr)
    assert len(slugs) == len(set(slugs)), f"duplicate slug in {module_name}.{attr}"


# ---------------------------------------------------------------------------
# Ashby — the signals a degraded run now emits
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_ashby.get_param")
@patch("scrape_ashby.get_supabase")
def test_ashby_ssm_fallback_is_logged_and_reported(mock_db, mock_param, caplog):
    """The scenario that started this: SSM unreadable. It used to be a bare
    `except Exception` with no log line at all."""
    import scrape_ashby

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.side_effect = RuntimeError("ParameterNotFound")
    respx.get(url__regex=r".*api\.ashbyhq\.com/posting-api/job-board/.*").mock(
        return_value=httpx.Response(200, json=_ashby_board())
    )

    result = scrape_ashby.handler({"query_hash": "h"}, {})

    assert result["config_source"] == "default"
    assert result["boards_configured"] == len(scrape_ashby.DEFAULT_COMPANIES)
    fallback = [m for m in _errors(caplog) if "SSM_FALLBACK" in m]
    assert fallback, "an SSM fallback must not be silent"
    assert "ParameterNotFound" in fallback[0]


@respx.mock
@patch("scrape_ashby.get_param")
@patch("scrape_ashby.get_supabase")
def test_ashby_404_is_an_error_not_a_warning(mock_db, mock_param, caplog):
    """A 404 is a permanent config error — the board will never come back on
    its own — so it is louder than a transient failure and greppable."""
    import scrape_ashby

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["linear", "deadslug"])
    respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").mock(
        return_value=httpx.Response(200, json=_ashby_board(2))
    )
    respx.get("https://api.ashbyhq.com/posting-api/job-board/deadslug").mock(
        return_value=httpx.Response(404)
    )

    result = scrape_ashby.handler({"query_hash": "h"}, {})

    assert result["count"] == 2          # still "succeeds" — hence the rest
    assert result["boards_ok"] == 1
    assert result["boards_configured"] == 2
    assert result["boards_failed"] == [
        {"company": "deadslug", "status": 404, "reason": "not_found"}
    ]
    assert result["boards_ok"] + len(result["boards_failed"]) == result["boards_configured"]
    errors = _errors(caplog)
    assert any("BOARD_NOT_FOUND" in m and "deadslug" in m for m in errors)
    # The summary line must not read like an unqualified success.
    assert any("FAILED" in m for m in errors)


@respx.mock
@patch("scrape_ashby.get_param")
@patch("scrape_ashby.get_supabase")
def test_ashby_transient_failure_stays_a_warning(mock_db, mock_param, caplog):
    import scrape_ashby

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["linear"])
    respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").mock(
        return_value=httpx.Response(503)
    )

    result = scrape_ashby.handler({"query_hash": "h"}, {})

    assert result["boards_ok"] == 0
    assert result["boards_failed"] == [
        {"company": "linear", "status": 503, "reason": "http_error"}
    ]
    assert any("BOARD_UNAVAILABLE" in m for m in _warnings(caplog))
    # A blip must not page: nothing on this path reaches ERROR.
    assert _errors(caplog) == []


@respx.mock
@patch("scrape_ashby.get_param")
@patch("scrape_ashby.get_supabase")
def test_ashby_empty_board_is_flagged(mock_db, mock_param, caplog):
    """200 with zero postings — the shape `vercel` was in during the audit.
    It answers, so it is not a failure, but it is worth seeing."""
    import scrape_ashby

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["emptyco"])
    respx.get("https://api.ashbyhq.com/posting-api/job-board/emptyco").mock(
        return_value=httpx.Response(200, json=_ashby_board(0))
    )

    result = scrape_ashby.handler({"query_hash": "h"}, {})

    assert result["boards_ok"] == 1
    assert result["boards_failed"] == []
    assert any("BOARD_EMPTY" in m for m in _warnings(caplog))


@respx.mock
@patch("scrape_ashby.get_param")
@patch("scrape_ashby.get_supabase")
def test_ashby_healthy_run_logs_no_errors(mock_db, mock_param, caplog):
    import scrape_ashby

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["linear", "notion"])
    respx.get(url__regex=r".*api\.ashbyhq\.com/posting-api/job-board/.*").mock(
        return_value=httpx.Response(200, json=_ashby_board(3))
    )

    result = scrape_ashby.handler({"query_hash": "h"}, {})

    assert result["config_source"] == "ssm"
    assert result["boards_ok"] == 2
    assert result["boards_failed"] == []
    assert _errors(caplog) == []


# ---------------------------------------------------------------------------
# Greenhouse — same contract
# ---------------------------------------------------------------------------

@respx.mock
@patch("scrape_greenhouse.get_param")
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_ssm_fallback_is_logged_and_reported(mock_db, mock_param, caplog):
    import scrape_greenhouse

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.side_effect = RuntimeError("ParameterNotFound")
    respx.get(url__regex=r".*boards-api\.greenhouse\.io/v1/boards/.*").mock(
        return_value=httpx.Response(200, json=_greenhouse_board())
    )

    result = scrape_greenhouse.handler({"query_hash": "h"}, {})

    assert result["config_source"] == "default"
    assert result["boards_configured"] == len(scrape_greenhouse.DEFAULT_BOARDS)
    assert any("SSM_FALLBACK" in m for m in _errors(caplog))


@respx.mock
@patch("scrape_greenhouse.get_param")
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_404_is_an_error_not_a_warning(mock_db, mock_param, caplog):
    import scrape_greenhouse

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["stripe", "deadslug"])
    respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true").mock(
        return_value=httpx.Response(200, json=_greenhouse_board(2))
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/deadslug/jobs?content=true").mock(
        return_value=httpx.Response(404)
    )

    result = scrape_greenhouse.handler({"query_hash": "h"}, {})

    assert result["boards_ok"] == 1
    assert result["boards_failed"] == [
        {"board": "deadslug", "status": 404, "reason": "not_found"}
    ]
    assert any("BOARD_NOT_FOUND" in m and "deadslug" in m for m in _errors(caplog))


@respx.mock
@patch("scrape_greenhouse.get_param")
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_healthy_run_logs_no_errors(mock_db, mock_param, caplog):
    import scrape_greenhouse

    caplog.set_level(logging.DEBUG)
    mock_db.return_value = _make_db(count=0)
    mock_param.return_value = json.dumps(["stripe"])
    respx.get(url__regex=r".*boards-api\.greenhouse\.io/v1/boards/.*").mock(
        return_value=httpx.Response(200, json=_greenhouse_board(3))
    )

    result = scrape_greenhouse.handler({"query_hash": "h"}, {})

    assert result["boards_ok"] == 1
    assert result["boards_failed"] == []
    assert _errors(caplog) == []


# ---------------------------------------------------------------------------
# Live re-verification — opt-in, skipped in CI
# ---------------------------------------------------------------------------

def _ashby_default_slugs():
    import scrape_ashby
    return scrape_ashby.DEFAULT_COMPANIES


def _greenhouse_default_slugs():
    import scrape_greenhouse
    return scrape_greenhouse.DEFAULT_BOARDS


@live_only
@pytest.mark.parametrize("slug", _ashby_default_slugs() if LIVE_CHECK else [])
def test_live_ashby_default_board_answers(slug):
    resp = httpx.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=30
    )
    assert resp.status_code == 200, f"{slug}: HTTP {resp.status_code}"
    jobs = resp.json().get("jobs", [])
    assert jobs, f"{slug}: 200 but 0 postings"


@live_only
@pytest.mark.parametrize("slug", _greenhouse_default_slugs() if LIVE_CHECK else [])
def test_live_greenhouse_default_board_answers(slug):
    resp = httpx.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=30
    )
    assert resp.status_code == 200, f"{slug}: HTTP {resp.status_code}"
    jobs = resp.json().get("jobs", [])
    assert jobs, f"{slug}: 200 but 0 postings"
