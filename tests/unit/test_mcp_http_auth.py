"""Unit tests for mcp_server.http_auth.RequireSupabaseJWT.

The mounted MCP transport (`app.mount("/mcp", ...)`) is a bare ASGI
sub-application, which FastAPI's route-level `Depends(get_current_user)`
never runs for (see http_auth.py's module docstring for why). These tests
drive the ASGI wrapper directly with fake scope/receive/send callables --
no FastAPI TestClient needed -- to prove the gate actually rejects
unauthenticated traffic before it reaches the wrapped app.
"""
import time
from unittest.mock import AsyncMock

import pytest
from jose import jwt

from mcp_server.http_auth import RequireSupabaseJWT

SECRET = "test-supabase-jwt-secret"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    # auth._get_jwks caches across calls; irrelevant for HS256 tokens but
    # reset defensively in case another test polluted it.
    import auth

    auth._jwks_cache = None


def _valid_token() -> str:
    claims = {"sub": "user-123", "email": "user@example.com", "aud": "authenticated", "exp": int(time.time()) + 3600}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "method": "GET", "path": "/mcp/sse", "headers": headers}


async def _run(app, scope):
    receive = AsyncMock(return_value={"type": "http.disconnect"})
    send = AsyncMock()
    await app(scope, receive, send)
    return send


@pytest.mark.asyncio
async def test_missing_authorization_header_is_rejected_before_reaching_the_app():
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)

    send = await _run(gated, _http_scope([]))

    inner.assert_not_called()
    status = send.call_args_list[0].args[0]["status"]
    assert status == 401


@pytest.mark.asyncio
async def test_garbage_token_is_rejected_before_reaching_the_app():
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)

    scope = _http_scope([(b"authorization", b"Bearer not-a-real-jwt")])
    send = await _run(gated, scope)

    inner.assert_not_called()
    status = send.call_args_list[0].args[0]["status"]
    assert status == 401


@pytest.mark.asyncio
async def test_valid_supabase_jwt_reaches_the_wrapped_app():
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)

    scope = _http_scope([(b"authorization", f"Bearer {_valid_token()}".encode())])
    receive = AsyncMock(return_value={"type": "http.disconnect"})
    send = AsyncMock()
    await gated(scope, receive, send)

    inner.assert_called_once_with(scope, receive, send)
    send.assert_not_called()


@pytest.mark.asyncio
async def test_non_http_scope_passes_through_without_auth():
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)

    scope = {"type": "lifespan"}
    receive = AsyncMock()
    send = AsyncMock()
    await gated(scope, receive, send)

    inner.assert_called_once_with(scope, receive, send)
