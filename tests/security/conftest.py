"""Shared fixtures for security tests.

Mocks heavy imports (AI client, scrapers, etc.) so app.py can be imported
without requiring actual credentials or external services.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Set required env vars BEFORE importing anything that reads them
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "super-secret-jwt-key-for-testing-only-32chars!")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")


@pytest.fixture()
def mock_db():
    """A MagicMock standing in for SupabaseClient."""
    db = MagicMock()
    # get_runs returns a list of run dicts
    db.get_runs.return_value = []
    # get_jobs returns (list, total)
    db.get_jobs.return_value = ([], 0)
    # get_user returns a user dict or None
    db.get_user.return_value = {
        "id": "user-123",
        "email": "test@example.com",
        "name": "Test User",
        "plan": "free",
        "created_at": "2026-01-01T00:00:00",
    }
    # client attribute for raw table access
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.gte.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.delete.return_value = mock_table

    execute_result = MagicMock()
    execute_result.data = []
    execute_result.count = 0
    mock_table.execute.return_value = execute_result

    db.client = MagicMock()
    db.client.table.return_value = mock_table
    return db


@pytest.fixture()
def mock_ai_client():
    """A MagicMock standing in for AIClient."""
    return MagicMock()


@pytest.fixture()
def _patch_app_globals(mock_db, mock_ai_client):
    """Patch app-level globals so endpoints work without real services."""
    import app as app_module

    app_module._db = mock_db
    app_module._ai_client = mock_ai_client
    app_module._resumes = {"sre_devops": r"\documentclass{article}\begin{document}Hello\end{document}"}
    app_module._config = {"resumes": {"sre_devops": {"tex_path": "fake.tex"}}}
    yield
    # Restore
    app_module._db = None
    app_module._ai_client = None
    app_module._resumes = {}
    app_module._config = {}


@pytest.fixture()
def client(_patch_app_globals):
    """FastAPI TestClient with mocked globals."""
    from fastapi.testclient import TestClient
    from app import app

    return TestClient(app, raise_server_exceptions=False)


def _make_hs256_jwt(payload: dict, secret: str | None = None) -> str:
    """Create an HS256-signed JWT for testing."""
    from jose import jwt as jose_jwt

    secret = secret or os.environ["SUPABASE_JWT_SECRET"]
    default_payload = {
        "sub": "user-123",
        "email": "test@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": 9999999999,  # far future
        "iat": 1700000000,
    }
    default_payload.update(payload)
    return jose_jwt.encode(default_payload, secret, algorithm="HS256")


@pytest.fixture()
def valid_token():
    """A valid HS256 JWT with sub and email."""
    return _make_hs256_jwt({})


@pytest.fixture()
def auth_headers(valid_token):
    """Authorization headers with a valid JWT."""
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture()
def no_auth_headers():
    """Empty headers (no Authorization)."""
    return {}


@pytest.fixture()
def expired_token():
    """A JWT with an expiration date in the past."""
    return _make_hs256_jwt({"exp": 1000000000})  # year 2001


@pytest.fixture()
def no_sub_token():
    """A JWT that is valid but has no 'sub' claim (service_role style)."""
    return _make_hs256_jwt({"sub": None})
