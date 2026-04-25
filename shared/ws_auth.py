"""Short-lived JWT for WebSocket upgrade, with split audience per role.

Two distinct audiences prevent a stolen token issued for one side
(frontend/browser) from being replayed as the other. start-session issues
both tokens up front — the frontend token goes back in the HTTP response,
the browser token goes to Fargate via WS_TOKEN env var."""

from __future__ import annotations

import os
import time

from jose import JWTError, jwt

_ALG = "HS256"
_ROLES = ("frontend", "browser")
_DEFAULT_TTL = 60


def _aud_for(role: str) -> str:
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}, got {role!r}")
    return f"ws.{role}"


def _secret() -> str:
    s = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not s:
        raise RuntimeError("SUPABASE_JWT_SECRET not set")
    return s


def issue_ws_token(*, user_id: str, session_id: str, role: str, ttl_seconds: int = _DEFAULT_TTL) -> str:
    """Issue a short-lived WebSocket upgrade JWT for the given role.

    Args:
        user_id: Supabase user UUID.
        session_id: Browser session ID linking frontend and Fargate worker.
        role: Either "frontend" or "browser" — determines the aud claim.
        ttl_seconds: Token lifetime in seconds (default 60).

    Returns:
        Signed JWT string.
    """
    aud = _aud_for(role)
    now = int(time.time())
    payload = {
        "sub": user_id,
        "session": session_id,
        "aud": aud,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALG)


def verify_ws_token(token: str, *, expected_role: str) -> dict:
    """Decode and verify a WebSocket upgrade JWT.

    Raises ValueError on any failure (expired, wrong audience, bad signature).
    The expected_role must match the aud claim exactly — cross-role replay is rejected.

    Args:
        token: The JWT string to verify.
        expected_role: The role this token should have been issued for.

    Returns:
        Decoded claims dict on success.
    """
    expected_aud = _aud_for(expected_role)
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALG], audience=expected_aud)
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise ValueError("token expired") from e
        if "audience" in msg:
            raise ValueError("invalid audience") from e
        raise ValueError(f"invalid token: {e}") from e
    if not claims.get("sub") or not claims.get("session"):
        raise ValueError("token missing required claims")
    return claims
