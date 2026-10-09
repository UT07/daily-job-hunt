"""Every MCP tool must act as the authenticated caller, never as a fixed account.

Audited 2026-10-08: `app.py` mounts the MCP transport behind
`RequireSupabaseJWT`, which only checked that a JWT was *valid*. Every tool in
`mcp_server/server.py` then ran as a hard-coded `DEFAULT_USER_ID` — the owner.
The web app exposes signUp, so any stranger could create an account, get a
valid JWT, and read the owner's private job-hunt data and spend the owner's
LLM budget scoring against the owner's résumé.

These tests drive the tools with a FAKE Supabase that holds rows for two users
and filters on whatever `user_id` the code asks for, so a test only passes if
the code asked for the right one (the double is checked for soundness at the
bottom, CLAUDE.md rule 6). The last test runs the real SSE transport over a
real socket with two real JWTs, because the identity has to survive the hop
from the HTTP request into the MCP session's task group, and only the real
transport can show that it does (CLAUDE.md rule 5).
"""
from __future__ import annotations

import asyncio
import pathlib
import socket
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt

from mcp_server import identity, server
from mcp_server.http_auth import RequireSupabaseJWT

SECRET = "test-supabase-jwt-secret"
USER_A = "aaaaaaaa-0000-0000-0000-00000000000a"
USER_B = "bbbbbbbb-0000-0000-0000-00000000000b"
OWNER_UUID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
REPO = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# A Supabase double that actually filters, so asking for the wrong user fails
# ---------------------------------------------------------------------------

TABLES = {
    "jobs": [
        {"job_hash": "a-1", "title": "Python Engineer at A-Corp", "company": "A-Corp", "description": "python",
         "match_score": 90, "score_tier": "S", "user_id": USER_A},
        {"job_hash": "b-1", "title": "Python Engineer at B-Corp", "company": "B-Corp", "description": "python",
         "match_score": 80, "score_tier": "A", "user_id": USER_B},
    ],
    "user_resumes": [
        {"tex_content": "RESUME OF USER A", "user_id": USER_A, "created_at": "2026-01-01"},
        {"tex_content": "RESUME OF USER B", "user_id": USER_B, "created_at": "2026-01-01"},
    ],
    "users": [
        {"id": USER_A, "work_authorizations": {}, "location": "Dublin"},
        {"id": USER_B, "work_authorizations": {}, "location": "Dublin"},
    ],
}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)
        self._single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def ilike(self, col, pattern):
        needle = pattern.strip("%").lower()
        self._rows = [r for r in self._rows if needle in str(r.get(col, "")).lower()]
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        if self._single:
            return _Result(self._rows[0] if self._rows else None)
        return _Result(self._rows)


class _RpcMissing:
    def execute(self):
        raise Exception("PGRST202 Could not find the function public.match_jobs_semantic in the schema cache")


class FakeDB:
    def table(self, name):
        return _Query(TABLES[name])

    def rpc(self, *_a, **_k):
        return _RpcMissing()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    import auth

    auth._jwks_cache = None
    server.reset_semantic_probe()
    with patch.object(server, "_db", return_value=FakeDB()):
        yield
    server.reset_semantic_probe()


def _token(sub: str) -> str:
    claims = {"sub": sub, "email": f"{sub}@example.com", "aud": "authenticated", "exp": int(time.time()) + 3600}
    return jwt.encode(claims, SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Tools: no identity, no answer. With identity, only that identity's data.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_with_no_caller_identity_refuses_instead_of_defaulting():
    with pytest.raises(identity.NoCallerIdentity):
        await server.search_jobs("python")
    with pytest.raises(identity.NoCallerIdentity):
        await server.get_job("a-1")
    with patch.object(server, "score_single_job_deterministic", return_value={"match_score": 70.0}) as scorer:
        with pytest.raises(identity.NoCallerIdentity):
            await server.score_job("Some JD")
    scorer.assert_not_called()  # no LLM spend for an anonymous caller


def test_the_owners_account_is_not_hardcoded_anywhere_in_the_mcp_server():
    assert not hasattr(server, "DEFAULT_USER_ID")
    for path in (REPO / "mcp_server").glob("*.py"):
        assert OWNER_UUID not in path.read_text(), f"{path.name} still names the owner's account"


@pytest.mark.asyncio
async def test_search_jobs_as_user_b_sees_only_user_b_rows():
    with identity.acting_as(USER_B):
        rows = await server.search_jobs("python")
    assert [r["job_hash"] for r in rows] == ["b-1"]


@pytest.mark.asyncio
async def test_get_job_as_user_b_cannot_read_user_a_job():
    with identity.acting_as(USER_B):
        assert await server.get_job("a-1") is None
        assert (await server.get_job("b-1"))["job_hash"] == "b-1"


@pytest.mark.asyncio
async def test_score_job_as_user_b_scores_against_user_b_resume():
    with patch.object(server, "score_single_job_deterministic", return_value={"match_score": 70.0}) as scorer:
        with identity.acting_as(USER_B):
            await server.score_job("Some JD", location="Dublin, Ireland")
    assert scorer.call_args.args[1] == "RESUME OF USER B"


def test_acting_as_restores_the_previous_identity():
    assert identity.current_user_id_or_none() is None
    with identity.acting_as(USER_A):
        with identity.acting_as(USER_B):
            assert identity.caller_user_id() == USER_B
        assert identity.caller_user_id() == USER_A
    assert identity.current_user_id_or_none() is None


# ---------------------------------------------------------------------------
# The ASGI gate: the verified `sub` is what the wrapped app sees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_gate_hands_the_verified_sub_to_the_wrapped_app():
    seen = {}

    async def inner(scope, receive, send):
        seen["user_id"] = identity.current_user_id_or_none()
        seen["scope_user"] = scope["user"].access_token.subject

    gated = RequireSupabaseJWT(inner)
    scope = {"type": "http", "method": "GET", "path": "/mcp/sse",
             "headers": [(b"authorization", f"Bearer {_token(USER_B)}".encode())]}
    await gated(scope, AsyncMock(return_value={"type": "http.disconnect"}), AsyncMock())

    assert seen == {"user_id": USER_B, "scope_user": USER_B}
    assert identity.current_user_id_or_none() is None, "identity leaked out of the request"


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, b"Bearer not-a-jwt"])
async def test_the_gate_rejects_missing_or_invalid_tokens_without_setting_identity(header):
    inner = AsyncMock()
    gated = RequireSupabaseJWT(inner)
    headers = [(b"authorization", header)] if header else []
    send = AsyncMock()
    await gated({"type": "http", "method": "GET", "path": "/mcp/sse", "headers": headers},
                AsyncMock(), send)
    inner.assert_not_called()
    assert send.call_args_list[0].args[0]["status"] == 401


# ---------------------------------------------------------------------------
# stdio: an explicit, documented user, never a silent default
# ---------------------------------------------------------------------------


def test_stdio_refuses_to_start_without_an_explicit_user(monkeypatch):
    monkeypatch.delenv(server.STDIO_USER_ENV, raising=False)
    with patch.object(server, "build_server") as build:
        with pytest.raises(SystemExit) as exc:
            server.main()
    build.assert_not_called()
    assert server.STDIO_USER_ENV in str(exc.value)


def test_stdio_runs_as_the_named_user(monkeypatch):
    monkeypatch.setenv(server.STDIO_USER_ENV, USER_A)
    seen = {}

    class _Srv:
        def run(self):
            # FastMCP.run() enters anyio.run(); the identity must survive that hop.
            import anyio

            async def probe():
                seen["user"] = identity.current_user_id_or_none()

            anyio.run(probe)

    import contextvars

    # main() sets the identity for the rest of its process, which is right for
    # stdio and wrong for a test runner: run it in a copied context so it
    # cannot leak into later tests and make them pass for the wrong reason.
    with patch.object(server, "build_server", return_value=_Srv()):
        contextvars.copy_context().run(server.main)
    assert seen["user"] == USER_A
    assert identity.current_user_id_or_none() is None


def test_the_runbook_documents_the_stdio_user_variable():
    text = (REPO / "docs" / "runbooks" / "mcp-client-setup.md").read_text()
    assert server.STDIO_USER_ENV in text


# ---------------------------------------------------------------------------
# End to end over the real SSE transport and a real socket
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_mcp():
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    port = _free_port()
    app = Starlette(routes=[Mount("/mcp", app=RequireSupabaseJWT(server.build_server().sse_app()))])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


async def _call_tool(base: str, token: str, name: str, args: dict):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(f"{base}/mcp/sse", headers={"Authorization": f"Bearer {token}"}) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return await session.call_tool(name, args)


def test_over_real_sse_each_caller_sees_only_their_own_jobs(live_mcp):
    import json

    def hashes(result):
        assert not result.isError, result.content
        return sorted(json.loads(c.text)["job_hash"] for c in result.content)

    as_b = asyncio.run(_call_tool(live_mcp, _token(USER_B), "search_jobs", {"query": "python"}))
    as_a = asyncio.run(_call_tool(live_mcp, _token(USER_A), "search_jobs", {"query": "python"}))
    assert hashes(as_b) == ["b-1"]
    assert hashes(as_a) == ["a-1"]


def test_over_real_sse_another_user_cannot_post_into_a_session(live_mcp):
    """The SDK binds a session to `scope["user"]`; the gate must populate it,
    or user B could inject tool calls into user A's open session."""
    import httpx

    async def scenario():
        from mcp.client.sse import sse_client

        captured = {}
        async with sse_client(
            f"{live_mcp}/mcp/sse",
            headers={"Authorization": f"Bearer {_token(USER_A)}"},
            on_session_created=lambda sid: captured.setdefault("sid", sid),
        ):
            async with httpx.AsyncClient() as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
                url = f"{live_mcp}/mcp/messages/?session_id={captured['sid']}"
                as_b = await client.post(url, json=body, headers={"Authorization": f"Bearer {_token(USER_B)}"})
                as_a = await client.post(url, json=body, headers={"Authorization": f"Bearer {_token(USER_A)}"})
        return as_b.status_code, as_a.status_code

    as_b, as_a = asyncio.run(scenario())
    assert as_a == 202, "the double is unsound: the owner of the session could not post to it"
    assert as_b == 404


# ---------------------------------------------------------------------------
# CLAUDE.md rule 6: prove the double discriminates
# ---------------------------------------------------------------------------


def test_the_fake_db_actually_filters_by_user():
    db = FakeDB()
    assert [r["job_hash"] for r in db.table("jobs").select("*").eq("user_id", USER_A).execute().data] == ["a-1"]
    assert [r["job_hash"] for r in db.table("jobs").select("*").execute().data] == ["a-1", "b-1"]
    assert db.table("users").select("*").eq("id", USER_B).single().execute().data["id"] == USER_B
