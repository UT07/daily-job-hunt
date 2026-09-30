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

    inner.assert_called_once()
    called_scope, called_receive, called_send = inner.call_args.args
    assert called_scope is scope
    assert called_receive is receive
    # `send` is wrapped, not passed straight through — see _single_response and
    # test_a_second_response_from_the_mounted_app_is_dropped. Anything the app
    # sends still reaches the real `send`.
    assert called_send is not send
    await called_send({"type": "http.response.start", "status": 200, "headers": []})
    send.assert_called_once_with({"type": "http.response.start", "status": 200, "headers": []})


@pytest.mark.asyncio
async def test_a_second_response_from_the_mounted_app_is_dropped():
    """The SSE endpoint sends two responses, and one of them breaks the app.

    mcp 1.30's `handle_sse` (fastmcp/server.py) streams the session and then
    `return Response()`. Starlette dutifully sends that second, empty response
    on the same ASGI scope after the stream has already finished. Nothing
    notices — unless a `BaseHTTPMiddleware` is in the stack, and `app.py` has
    one (`AuditMiddleware`): its `body_stream` asserts every message after the
    first is `http.response.body` and dies on a second
    `http.response.start`.

    Measured on 2026-09-30 by running the real client against `uvicorn app:app`:
    every MCP session over /mcp/sse completed correctly AND logged
    "ERROR: Exception in ASGI application / AssertionError: Unexpected
    message: {'type': 'http.response.start', 'status': 200, ...}". A
    per-connection traceback in CloudWatch is exactly the noise real errors
    hide behind.

    This gate is the one piece of that path this repo owns, so it is where the
    duplicate gets dropped.
    """
    async def sends_twice(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"event: endpoint\n\n"})
        # mcp's `return Response()`, as Starlette delivers it
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    gated = RequireSupabaseJWT(sends_twice)
    scope = _http_scope([(b"authorization", f"Bearer {_valid_token()}".encode())])
    send = await _run(gated, scope)

    starts = [c.args[0] for c in send.call_args_list if c.args[0]["type"] == "http.response.start"]
    assert len(starts) == 1, "a second http.response.start reached the middleware"
    bodies = [c.args[0] for c in send.call_args_list if c.args[0]["type"] == "http.response.body"]
    assert [b["body"] for b in bodies] == [b"event: endpoint\n\n"], (
        "the second response's body was forwarded too"
    )


@pytest.mark.asyncio
async def test_a_normal_streaming_response_is_forwarded_intact():
    """Dropping too much would be worse than the traceback."""
    async def streams(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for chunk in (b"a", b"b", b"c"):
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    gated = RequireSupabaseJWT(streams)
    scope = _http_scope([(b"authorization", f"Bearer {_valid_token()}".encode())])
    send = await _run(gated, scope)

    forwarded = [c.args[0] for c in send.call_args_list]
    assert forwarded[0]["type"] == "http.response.start"
    assert [m["body"] for m in forwarded[1:]] == [b"a", b"b", b"c", b""]


@pytest.mark.asyncio
async def test_non_http_scope_passes_through_without_auth():
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)

    scope = {"type": "lifespan"}
    receive = AsyncMock()
    send = AsyncMock()
    await gated(scope, receive, send)

    inner.assert_called_once_with(scope, receive, send)
