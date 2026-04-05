"""Unit tests for POST /api/feedback/flag-score endpoint."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def _make_hs256_jwt(payload: dict) -> str:
    """Create an HS256-signed JWT for testing."""
    from jose import jwt as jose_jwt

    secret = os.environ["SUPABASE_JWT_SECRET"]
    default_payload = {
        "sub": "user-123",
        "email": "test@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": 9999999999,
        "iat": 1700000000,
    }
    default_payload.update(payload)
    return jose_jwt.encode(default_payload, secret, algorithm="HS256")


@pytest.fixture()
def mock_db():
    """A MagicMock standing in for SupabaseClient."""
    db = MagicMock()
    db.get_user.return_value = {
        "id": "user-123",
        "email": "test@example.com",
        "name": "Test User",
        "plan": "free",
        "created_at": "2026-01-01T00:00:00",
    }
    # Chain: db.client.table("pipeline_adjustments").insert({...}).execute()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": "adj-1"}])
    db.client = MagicMock()
    db.client.table.return_value = mock_table
    return db


@pytest.fixture()
def _patch_app(mock_db):
    """Patch app globals so endpoint works without real services."""
    import app as app_module

    app_module._db = mock_db
    app_module._ai_client = MagicMock()
    app_module._resumes = {"sre_devops": r"\documentclass{article}\begin{document}Hello\end{document}"}
    app_module._config = {"resumes": {"sre_devops": {"tex_path": "fake.tex"}}}
    yield
    app_module._db = None
    app_module._ai_client = None
    app_module._resumes = {}
    app_module._config = {}


@pytest.fixture()
def client(_patch_app):
    """FastAPI TestClient with mocked globals."""
    from fastapi.testclient import TestClient
    from app import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def auth_headers():
    """Authorization headers with a valid JWT."""
    token = _make_hs256_jwt({})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFlagScoreEndpoint:
    """POST /api/feedback/flag-score creates a pipeline_adjustments entry."""

    def test_flag_score_success(self, client, auth_headers, mock_db):
        """Valid request creates adjustment entry and returns ok."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={
                "job_id": "job-abc123",
                "feedback_type": "score_too_low",
                "expected_score": 85,
                "comment": "This job is a great match for my profile",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["message"] == "Feedback recorded"

        # Verify Supabase insert was called with correct data
        mock_db.client.table.assert_called_with("pipeline_adjustments")
        insert_call = mock_db.client.table.return_value.insert
        insert_call.assert_called_once()
        inserted = insert_call.call_args[0][0]
        assert inserted["user_id"] == "user-123"
        assert inserted["adjustment_type"] == "quality_flag"
        assert inserted["risk_level"] == "high"
        assert inserted["status"] == "pending"
        assert inserted["payload"]["job_id"] == "job-abc123"
        assert inserted["payload"]["feedback_type"] == "score_too_low"
        assert inserted["payload"]["expected_score"] == 85
        assert inserted["payload"]["comment"] == "This job is a great match for my profile"
        assert "job-abc123" in inserted["reason"]

    def test_flag_score_defaults(self, client, auth_headers, mock_db):
        """Minimal request uses defaults for optional fields."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": "job-xyz"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        inserted = mock_db.client.table.return_value.insert.call_args[0][0]
        assert inserted["payload"]["feedback_type"] == "score_inaccurate"
        assert inserted["payload"]["expected_score"] is None
        assert inserted["payload"]["comment"] is None

    def test_flag_score_missing_job_id(self, client, auth_headers):
        """Request without job_id returns 422 validation error."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={"feedback_type": "score_too_low"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_flag_score_empty_job_id(self, client, auth_headers):
        """Request with empty job_id returns 422 validation error."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_flag_score_no_auth(self, client):
        """Request without auth returns 401."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": "job-abc123"},
        )
        assert resp.status_code == 401

    def test_flag_score_db_not_configured(self, client, auth_headers):
        """Returns 503 when database is not configured."""
        import app as app_module
        app_module._db = None
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": "job-abc123"},
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_flag_score_db_error(self, client, auth_headers, mock_db):
        """Returns 500 when database insert fails."""
        mock_db.client.table.return_value.insert.return_value.execute.side_effect = Exception("DB error")
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": "job-abc123"},
            headers=auth_headers,
        )
        assert resp.status_code == 500
        assert "Failed to record feedback" in resp.json()["detail"]

    def test_flag_score_invalid_expected_score(self, client, auth_headers):
        """expected_score outside 0-100 range returns 422."""
        resp = client.post(
            "/api/feedback/flag-score",
            json={"job_id": "job-abc123", "expected_score": 150},
            headers=auth_headers,
        )
        assert resp.status_code == 422
