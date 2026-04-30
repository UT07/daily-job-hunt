"""Tests for the ``require_db`` FastAPI dependency in app.py.

The helper consolidates the ``if _db is None: raise HTTPException(503, ...)``
boilerplate that previously had to be repeated at the top of every endpoint
that touches the database. This regression suite locks in the two
behaviours the dependency is supposed to provide:

  1. Raise an HTTP 503 when ``_db`` is unset (e.g. cold-start before
     ``_initialize_state`` ran, or in a test environment with no Supabase
     creds).
  2. Return the live client otherwise so the calling endpoint can use it.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_require_db_raises_when_unset(monkeypatch):
    import app

    monkeypatch.setattr(app, "_db", None)
    with pytest.raises(HTTPException) as exc:
        app.require_db()
    assert exc.value.status_code == 503


def test_require_db_returns_client_when_set(monkeypatch):
    import app

    sentinel = object()
    monkeypatch.setattr(app, "_db", sentinel)
    assert app.require_db() is sentinel
