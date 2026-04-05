"""Security tests: authentication and authorization.

Verifies that:
- All protected endpoints return 401 without an auth token
- Invalid / expired JWTs return 401
- JWTs without a 'sub' claim return 401
"""

import pytest
from tests.security.conftest import _make_hs256_jwt

# ── Endpoints that require auth ──────────────────────────────────────────────
# Each tuple is (method, path, optional_json_body).

PROTECTED_ENDPOINTS = [
    ("POST", "/api/pipeline/run", {"queries": ["software engineer"]}),
    ("GET", "/api/pipeline/status", None),
    ("GET", "/api/dashboard/jobs", None),
    ("GET", "/api/dashboard/stats", None),
    ("GET", "/api/dashboard/runs", None),
    ("GET", "/api/profile", None),
    ("PUT", "/api/profile", {"full_name": "Test"}),
    ("POST", "/api/score", {
        "job_description": "We need a Python developer with 5 years experience in AWS and Docker.",
        "job_title": "Software Engineer",
        "company": "TestCo",
    }),
    ("POST", "/api/tailor", {
        "job_description": "We need a Python developer with 5 years experience in AWS and Docker.",
        "job_title": "Software Engineer",
        "company": "TestCo",
    }),
    ("POST", "/api/cover-letter", {
        "job_description": "We need a Python developer with 5 years experience in AWS and Docker.",
        "job_title": "Software Engineer",
        "company": "TestCo",
    }),
    ("POST", "/api/contacts", {
        "job_description": "We need a Python developer with 5 years experience in AWS and Docker.",
        "job_title": "Software Engineer",
        "company": "TestCo",
    }),
    ("GET", "/api/quality-stats", None),
    ("POST", "/api/resumes/upload", None),
    ("GET", "/api/resumes", None),
    ("POST", "/api/feedback/flag-score", {"job_id": "job-abc123"}),
]


# ── 1. Missing auth header → 401 ─────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", PROTECTED_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, _ in PROTECTED_ENDPOINTS])
def test_no_auth_returns_401(client, no_auth_headers, method, path, body):
    """Every protected endpoint must reject requests without an auth token."""
    kwargs = {"headers": no_auth_headers}
    if body is not None:
        kwargs["json"] = body

    if method == "POST" and path == "/api/resumes/upload":
        # Upload endpoint expects a file, not JSON
        resp = client.post(path, headers=no_auth_headers)
    else:
        resp = getattr(client, method.lower())(path, **kwargs)

    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} instead of 401 "
        f"when no auth header was provided"
    )


# ── 2. Invalid / garbage token → 401 ─────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", PROTECTED_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, _ in PROTECTED_ENDPOINTS])
def test_invalid_token_returns_401(client, method, path, body):
    """A random / garbage Bearer token must be rejected."""
    headers = {"Authorization": "Bearer not-a-real-jwt-token"}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body

    if method == "POST" and path == "/api/resumes/upload":
        resp = client.post(path, headers=headers)
    else:
        resp = getattr(client, method.lower())(path, **kwargs)

    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} instead of 401 "
        f"for an invalid JWT"
    )


# ── 3. Expired token → 401 ───────────────────────────────────────────────────

SAMPLE_PROTECTED = [
    ("POST", "/api/pipeline/run", {"queries": ["swe"]}),
    ("GET", "/api/pipeline/status", None),
    ("GET", "/api/dashboard/jobs", None),
    ("GET", "/api/profile", None),
    ("POST", "/api/score", {
        "job_description": "We need a Python developer with 5 years experience in AWS and Docker.",
        "job_title": "SE",
        "company": "X",
    }),
]


@pytest.mark.parametrize("method,path,body", SAMPLE_PROTECTED,
                         ids=[f"{m} {p}" for m, p, _ in SAMPLE_PROTECTED])
def test_expired_token_returns_401(client, expired_token, method, path, body):
    """An expired JWT must be rejected with 401."""
    headers = {"Authorization": f"Bearer {expired_token}"}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body

    resp = getattr(client, method.lower())(path, **kwargs)
    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} instead of 401 "
        f"for an expired token"
    )


# ── 4. Token without 'sub' claim → 401 ───────────────────────────────────────

@pytest.mark.parametrize("method,path,body", SAMPLE_PROTECTED,
                         ids=[f"{m} {p}" for m, p, _ in SAMPLE_PROTECTED])
def test_token_without_sub_returns_401(client, method, path, body):
    """A JWT signed correctly but missing 'sub' (service_role style) must be rejected."""
    # Create a token with sub=None — the auth module should reject it
    token = _make_hs256_jwt({"sub": ""})
    headers = {"Authorization": f"Bearer {token}"}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body

    resp = getattr(client, method.lower())(path, **kwargs)
    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} instead of 401 "
        f"for a token missing 'sub'"
    )


# ── 5. Wrong secret → 401 ────────────────────────────────────────────────────

def test_wrong_secret_returns_401(client):
    """A JWT signed with a different secret must be rejected."""
    token = _make_hs256_jwt({}, secret="wrong-secret-definitely-not-the-right-one!!")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/profile", headers=headers)
    assert resp.status_code == 401


# ── 6. Public endpoints remain accessible ────────────────────────────────────

def test_health_is_public(client):
    """GET /api/health must work without any auth."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_templates_is_public(client):
    """GET /api/templates must work without any auth."""
    # templates endpoint imports template_engine at call time;
    # it may fail with a module error, but it should NOT return 401.
    resp = client.get("/api/templates")
    # Accept 200 (works) or 500 (import error) — but NOT 401/403
    assert resp.status_code != 401
    assert resp.status_code != 403


# ── 7. Valid token succeeds (sanity check) ────────────────────────────────────

def test_valid_token_profile_succeeds(client, auth_headers):
    """A valid JWT should let the user access /api/profile."""
    resp = client.get("/api/profile", headers=auth_headers)
    # Should be 200 (mocked DB returns a user) or 503 if DB missing — not 401
    assert resp.status_code != 401, "Valid token was incorrectly rejected"
    assert resp.status_code in (200, 503)


def test_valid_token_dashboard_jobs_succeeds(client, auth_headers):
    """A valid JWT should let the user access /api/dashboard/jobs."""
    resp = client.get("/api/dashboard/jobs", headers=auth_headers)
    assert resp.status_code != 401, "Valid token was incorrectly rejected"
