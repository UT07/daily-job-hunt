"""Unit tests for shared.ws_auth — short-lived two-audience WebSocket JWTs."""
import os
import time
from unittest.mock import patch

import pytest

_SECRET = "unit-test-secret"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": _SECRET}):
        yield


def test_issue_frontend_then_verify_roundtrip():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend")
    claims = verify_ws_token(token, expected_role="frontend")
    assert claims["sub"] == "user-1"
    assert claims["session"] == "sess-9"
    assert claims["aud"] == "ws.frontend"


def test_issue_browser_then_verify_roundtrip():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="browser")
    claims = verify_ws_token(token, expected_role="browser")
    assert claims["aud"] == "ws.browser"


def test_frontend_token_rejected_for_browser_role():
    """CRITICAL: a stolen frontend token must not let an attacker claim role=browser."""
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend")
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(token, expected_role="browser")


def test_browser_token_rejected_for_frontend_role():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="browser")
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(token, expected_role="frontend")


def test_ws_token_expires_quickly():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend", ttl_seconds=1)
    # JWT exp claim is integer-second resolution. Sleep > 2s to guarantee
    # the integer second has rolled past `iat + ttl_seconds`.
    time.sleep(2.1)
    with pytest.raises(ValueError, match="expired"):
        verify_ws_token(token, expected_role="frontend")


def test_verify_rejects_regular_supabase_audience():
    """A regular API JWT (aud='authenticated') must not be usable on WS."""
    from jose import jwt
    from shared.ws_auth import verify_ws_token
    bad = jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 60},
        _SECRET, algorithm="HS256",
    )
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(bad, expected_role="frontend")


def test_verify_rejects_bad_signature():
    from jose import jwt
    from shared.ws_auth import verify_ws_token
    forged = jwt.encode(
        {"sub": "user-1", "aud": "ws.frontend", "session": "sess-1", "exp": int(time.time()) + 60},
        "WRONG-SECRET", algorithm="HS256",
    )
    with pytest.raises(ValueError):
        verify_ws_token(forged, expected_role="frontend")


def test_issue_rejects_unknown_role():
    from shared.ws_auth import issue_ws_token
    with pytest.raises(ValueError, match="role must be"):
        issue_ws_token(user_id="u", session_id="s", role="attacker")


def test_verify_rejects_unknown_expected_role():
    from shared.ws_auth import verify_ws_token
    with pytest.raises(ValueError, match="role must be"):
        verify_ws_token("any-token", expected_role="attacker")


def test_issue_rejects_missing_secret():
    with patch.dict(os.environ, {}, clear=True):
        from shared.ws_auth import issue_ws_token
        with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET"):
            issue_ws_token(user_id="u", session_id="s", role="frontend")
