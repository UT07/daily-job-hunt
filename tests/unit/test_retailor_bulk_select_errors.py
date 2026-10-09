"""select_jobs must fail loudly on a PostgREST error, not read it as "no jobs".

Neither request in select_jobs checked the status. An error answer is a JSON
OBJECT ({"code": ..., "message": ...}), so on the jobs query
`not isinstance(page, list)` ended the loop and the batch reported zero jobs;
on the jobs_raw query every row was dropped by `isinstance(r, dict)` and
every job was reported "not in jobs_raw, handler would raise". Both read as
a clean, empty run — the exact shape of an expired key or a renamed column.
"""
from __future__ import annotations

import importlib.util
import pathlib

import httpx
import pytest

_SCRIPT = pathlib.Path("scripts/retailor_bulk.py")
_spec = importlib.util.spec_from_file_location("retailor_bulk_select", _SCRIPT)
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

URL = "https://example.supabase.co"
GOOD = {"job_hash": "3dac0d143aaf", "canonical_hash": None, "title": "SRE",
        "company": "RELX", "score_tier": "S", "resume_s3_url": None}


def _response(status, body, path):
    return httpx.Response(status, json=body, request=httpx.Request("GET", f"{URL}{path}"))


def _router(jobs_answer, raw_answer):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/rest/v1/jobs"):
            return jobs_answer()
        if url.endswith("/rest/v1/jobs_raw"):
            return raw_answer()
        raise AssertionError(url)
    return fake_get


def test_healthy_path_returns_the_jobs(monkeypatch):
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(200, [GOOD], "/rest/v1/jobs"),
        lambda: _response(200, [{"job_hash": "3dac0d143aaf"}], "/rest/v1/jobs_raw")))
    out = rb.select_jobs(URL, {}, "u1", ["S"], None)
    assert [j["job_hash"] for j in out] == ["3dac0d143aaf"]


def test_an_http_error_on_the_jobs_query_raises(monkeypatch):
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(401, {"code": "PGRST301", "message": "JWT expired"}, "/rest/v1/jobs"),
        lambda: _response(200, [], "/rest/v1/jobs_raw")))
    with pytest.raises(httpx.HTTPStatusError):
        rb.select_jobs(URL, {}, "u1", ["S"], None)


def test_a_non_list_body_on_the_jobs_query_raises(monkeypatch):
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(200, {"message": "unexpected"}, "/rest/v1/jobs"),
        lambda: _response(200, [], "/rest/v1/jobs_raw")))
    with pytest.raises(RuntimeError, match="not a list"):
        rb.select_jobs(URL, {}, "u1", ["S"], None)


def test_an_http_error_on_the_jobs_raw_query_raises(monkeypatch):
    """Used to report every job as 'not in jobs_raw'."""
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(200, [GOOD], "/rest/v1/jobs"),
        lambda: _response(400, {"code": "42703", "message": "column does not exist"},
                          "/rest/v1/jobs_raw")))
    with pytest.raises(httpx.HTTPStatusError):
        rb.select_jobs(URL, {}, "u1", ["S"], None)


def test_a_non_list_body_on_the_jobs_raw_query_raises(monkeypatch):
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(200, [GOOD], "/rest/v1/jobs"),
        lambda: _response(200, {"message": "odd"}, "/rest/v1/jobs_raw")))
    with pytest.raises(RuntimeError, match="not a list"):
        rb.select_jobs(URL, {}, "u1", ["S"], None)


def test_an_empty_list_is_still_a_legitimate_empty_result(monkeypatch):
    monkeypatch.setattr(httpx, "get", _router(
        lambda: _response(200, [], "/rest/v1/jobs"),
        lambda: _response(200, [], "/rest/v1/jobs_raw")))
    assert rb.select_jobs(URL, {}, "u1", ["S"], None) == []
