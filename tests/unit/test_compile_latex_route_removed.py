"""`POST /api/compile-latex` is gone and must stay gone.

Audited 2026-10-08: it passed the request's LaTeX SOURCE to
`compile_tex_to_pdf`, which takes a PATH, so every call returned 500. Nothing
in web/src called it. A dead route that compiles caller-supplied LaTeX is
attack surface with no user (see shared/latex_safety.py for why compiling
untrusted LaTeX is dangerous), so it was deleted rather than repaired.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    monkeypatch.setattr(app_module, "_db", MagicMock())
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id="user-1", email="u@x.com")
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield TestClient(app_module.app), app_module
    app_module.app.dependency_overrides.clear()


def test_the_route_is_not_registered(client):
    _, app_module = client
    paths = {getattr(r, "path", None) for r in app_module.app.routes}
    assert "/api/compile-latex" not in paths
    assert "/api/resumes/upload" in paths, "the double is unsound: routes were not read"


def test_posting_to_it_is_not_found(client):
    http, _ = client
    r = http.post("/api/compile-latex", json={"tex_source": "\\documentclass{article}"})
    assert r.status_code in (404, 405)


def test_its_request_model_is_gone(client):
    _, app_module = client
    assert not hasattr(app_module, "CompileLatexRequest")
