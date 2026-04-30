"""Regression tests for F7 — search jobs by title.

Prior to this change `/api/dashboard/jobs` accepted `company` (substring
ilike on the company column) but no equivalent for `title`. The
Dashboard had no Title input either. After the fix:

- The endpoint accepts a `title` query param.
- `db_client.get_jobs` translates the `title` filter into
  ``query.ilike("title", f"%{value}%")``.

These tests pin the contract.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    db.get_jobs.return_value = ([], 0)
    monkeypatch.setattr(app_module, "_db", db)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com",
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def test_dashboard_jobs_endpoint_accepts_title_param(client):
    """GET /api/dashboard/jobs?title=Backend forwards title into filters."""
    c, db = client
    resp = c.get("/api/dashboard/jobs?title=Backend")
    assert resp.status_code == 200, resp.text
    db.get_jobs.assert_called_once()
    _, kwargs = db.get_jobs.call_args
    filters = kwargs["filters"]
    assert filters.get("title") == "Backend", (
        f"title filter not forwarded; got filters={filters!r}"
    )


def test_dashboard_jobs_endpoint_omits_title_when_blank(client):
    """An empty title param must not appear in filters at all."""
    c, db = client
    resp = c.get("/api/dashboard/jobs")
    assert resp.status_code == 200, resp.text
    db.get_jobs.assert_called_once()
    _, kwargs = db.get_jobs.call_args
    filters = kwargs["filters"]
    assert "title" not in filters


def test_db_get_jobs_applies_ilike_on_title():
    """db_client.get_jobs translates filters.title into query.ilike(title, %v%)."""
    from db_client import SupabaseClient

    sb = SupabaseClient.__new__(SupabaseClient)

    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.ilike.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.execute.return_value = MagicMock(data=[], count=0)

    sb.client = MagicMock()
    sb.client.table.return_value = chain

    sb.get_jobs("user-1", filters={"title": "Backend Engineer"})

    # Must have called ilike on the "title" column with %Backend Engineer%
    title_calls = [
        call for call in chain.ilike.call_args_list
        if call.args and call.args[0] == "title"
    ]
    assert title_calls, (
        f"Expected query.ilike('title', ...) but got: {chain.ilike.call_args_list!r}"
    )
    assert title_calls[0].args[1] == "%Backend Engineer%"
