"""A scraper whose every request failed must say so, not report a quiet market.

Each handler used to return {"count": 0} with no `error` when every search
request failed (proxy down, key revoked, LinkedIn 999). save_metrics stores
result.get("error") per scraper, so that run was indistinguishable from a
day with no new postings.

For every scraper below: all requests failing -> `error` set (and labelled
auth_failed when every failure was 401/403/407/999 or a login wall); requests
succeeding with zero results -> count 0 and NO error. The handlers return the
error rather than raising, so the parallel scrape state is not retried or
replaced by a generic "scraper_failed".
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, "lambdas/scrapers")
sys.path.insert(0, ".")

import scrape_adzuna  # noqa: E402
import scrape_glassdoor  # noqa: E402
import scrape_indeed  # noqa: E402
import scrape_irish  # noqa: E402
import scrape_linkedin  # noqa: E402
from request_tally import RequestTally  # noqa: E402


def _db():
    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table
    for m in ("select", "eq", "gte", "in_", "upsert"):
        getattr(table, m).return_value = table
    res = MagicMock()
    res.count = 0
    res.data = []
    table.execute.return_value = res
    return db


def _resp(status, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = {"results": [], "search": {"documents": []}}
    return r


EVENT = {"queries": ["software engineer", "sre"], "query_hash": "qh", "locations": ["Ireland"]}


def _run(module, responder):
    """Run a handler with every outbound search request answered by responder."""
    def fake_get(*a, **k):
        r = responder()
        if isinstance(r, Exception):
            raise r
        return r

    client = MagicMock()
    client.get.side_effect = fake_get
    client.post.side_effect = fake_get
    with patch.object(module, "get_supabase", return_value=_db()), \
         patch.object(module, "get_param", return_value="proxy"), \
         patch.object(module.httpx, "get", side_effect=fake_get), \
         patch.object(module.httpx, "Client", return_value=client):
        return module.handler(dict(EVENT), None)


SCRAPERS = [scrape_linkedin, scrape_indeed, scrape_glassdoor, scrape_adzuna, scrape_irish]
IDS = [m.__name__ for m in SCRAPERS]


@pytest.mark.parametrize("module", SCRAPERS, ids=IDS)
def test_every_request_timing_out_is_an_error(module):
    out = _run(module, lambda: httpx.ReadTimeout("timed out"))
    assert out["count"] == 0
    assert out.get("error", "").startswith("all_requests_failed"), out
    assert "ReadTimeout" in out["error"]


@pytest.mark.parametrize("module", SCRAPERS, ids=IDS)
def test_every_request_refused_is_an_auth_error(module):
    out = _run(module, lambda: _resp(403))
    assert out["count"] == 0
    assert out.get("error", "").startswith("auth_failed"), out


@pytest.mark.parametrize("module", SCRAPERS, ids=IDS)
def test_a_quiet_market_is_not_an_error(module):
    """Requests succeed and return nothing: zero jobs, and no error."""
    out = _run(module, lambda: _resp(200, "<html><body>no results</body></html>"))
    assert out["count"] == 0
    assert "error" not in out, out
    assert out["requests"] > 0 and out["failed_requests"] == 0


def test_linkedin_999_is_an_auth_failure():
    out = _run(scrape_linkedin, lambda: _resp(999))
    assert out["error"].startswith("auth_failed") and "HTTP 999" in out["error"]


def test_partial_failure_is_counted_but_not_an_error():
    answers = iter([_resp(500), _resp(200, "<html></html>")])
    out = _run(scrape_linkedin, lambda: next(answers))
    assert "error" not in out
    assert out["requests"] == 2 and out["failed_requests"] == 1


def test_glassdoor_login_wall_is_an_auth_failure():
    with patch.object(scrape_glassdoor, "_has_login_wall", return_value=True):
        out = _run(scrape_glassdoor, lambda: _resp(200, "<html>sign in</html>"))
    assert out["error"].startswith("auth_failed"), out


def test_irish_reports_per_site_failure_when_only_one_site_is_down():
    calls = {"n": 0}

    def responder():
        calls["n"] += 1
        return _resp(403) if calls["n"] <= 2 else _resp(200, "<html></html>")

    out = _run(scrape_irish, responder)
    assert "error" not in out
    assert "jobs_ie" in out.get("site_errors", {}), out


def test_irish_is_not_an_error_while_gradireland_still_works():
    """The 179-day shape: both StepStone hosts dead, gradireland carrying the run."""
    calls = {"n": 0}

    def answer():
        calls["n"] += 1  # 2 queries x (jobs_ie, irishjobs), then gradireland
        return _resp(403) if calls["n"] <= 4 else _resp(200, "{}")

    out = _run(scrape_irish, answer)
    assert "error" not in out, out
    assert set(out["site_errors"]) == {"jobs_ie", "irishjobs"}
    assert out["requests"] == 6 and out["failed_requests"] == 4


def test_the_tally_needs_an_attempt_before_it_can_call_anything_failed():
    t = RequestTally("x")
    assert t.error() is None
    assert t.annotate({})["requests"] == 0


def test_a_mixed_failure_is_not_labelled_auth():
    t = RequestTally("x")
    t.http_failure(403)
    t.exception(httpx.ReadTimeout("t"))
    assert t.error().startswith("all_requests_failed")
