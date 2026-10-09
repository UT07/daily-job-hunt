#!/usr/bin/env python3
"""Connect a real MCP client to this server and call a tool. No mocks.

CLAUDE.md verification rule 5: for anything crossing a boundary, verify once
against the real thing. The MCP transport had never been connected by
anything, so every claim about it was a claim about code that had never run
end to end. This script is the thing that runs it.

Two transports, both driven by the official `mcp` client SDK — the same
library Claude Desktop and Claude Code speak:

  stdio  spawns `python -m mcp_server.server` and talks over its pipes. This
         is what a local Claude Code / Claude Desktop entry uses. No auth:
         the client owns the process, and NAUKRIBABA_MCP_USER_ID names the
         Supabase user it acts as. Required: the server will not start
         without it, and this script exits up front if it is unset.

  sse    starts uvicorn on 127.0.0.1, mints an HS256 Supabase JWT from
         SUPABASE_JWT_SECRET (the pattern tests/unit/test_mcp_http_auth.py
         and scripts/verify_phase1.py already use) and connects through
         app.py's mounted, JWT-gated /mcp/sse. This exercises
         RequireSupabaseJWT and the SDK's DNS-rebinding host check as well as
         the tools.

Both need the Supabase credentials in .env (SUPABASE_URL,
SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET) because `get_job` reads the live
`jobs` table — that is the point; a stubbed DB would prove nothing the unit
tests do not already prove.

Usage:
    .venv/bin/python scripts/mcp_client_check.py              # both transports
    .venv/bin/python scripts/mcp_client_check.py --stdio
    .venv/bin/python scripts/mcp_client_check.py --sse
    .venv/bin/python scripts/mcp_client_check.py --job-hash <hash>

Read-only: it lists tools and calls `get_job` / `search_jobs`. It does not
call `score_job`, which would spend three uncached model calls.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import pathlib
import socket
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
EXPECTED_TOOLS = {"search_jobs", "score_job", "get_job"}


def load_env() -> None:
    """Explicit, not an import side effect — CLAUDE.md verification rule 8."""
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


# Same name the server reads (mcp_server/server.py STDIO_USER_ENV), pinned by
# tests/unit/test_mcp_client_check_stdio_user.py. A literal, not an import:
# importing mcp_server.server here would pull in Supabase and model clients
# before the check that is meant to fail fast.
STDIO_USER_ENV = "NAUKRIBABA_MCP_USER_ID"


def require_stdio_user() -> str:
    """The user the stdio server will act as, or exit naming what is missing.

    The server refuses to start without it. Spawning it anyway showed the
    operator a closed pipe from a child that had already exited, with nothing
    naming the variable; this says so before any work starts.
    """
    user_id = os.environ.get(STDIO_USER_ENV, "").strip()
    if not user_id:
        raise SystemExit(
            f"{STDIO_USER_ENV} is not set, and the stdio transport needs it: the "
            "MCP server acts as exactly that Supabase user and refuses to start "
            "without one. Put it in .env (this script loads .env and passes it to "
            "the child), export it, or run with --sse only. "
            "See docs/runbooks/mcp-client-setup.md."
        )
    return user_id


def mint_jwt() -> str:
    from jose import jwt

    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise SystemExit("SUPABASE_JWT_SECRET is not set — cannot mint a test token")
    now = int(time.time())
    return jwt.encode(
        {"sub": USER_ID, "email": "254utkarsh@gmail.com", "aud": "authenticated",
         "role": "authenticated", "iat": now, "exp": now + 900},
        secret,
        algorithm="HS256",
    )


def a_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SCORE_JD = (
    "Senior Site Reliability Engineer, Dublin (hybrid). You will own our "
    "Kubernetes platform on AWS, Terraform everything, and run the on-call "
    "rotation. 5+ years in SRE or platform engineering, strong Python or Go, "
    "Prometheus and Grafana. Irish/EU work authorisation required."
)


async def exercise(session, label: str, job_hash: str | None, score: bool = False) -> None:
    await session.initialize()
    tools = {t.name for t in (await session.list_tools()).tools}
    print(f"[{label}] tools/list -> {sorted(tools)}")
    missing = EXPECTED_TOOLS - tools
    if missing:
        raise SystemExit(f"[{label}] missing tools: {sorted(missing)}")

    search = await session.call_tool("search_jobs", {"query": "platform engineer", "limit": 3})
    text = search.content[0].text if search.content else ""
    print(f"[{label}] search_jobs -> isError={search.isError} {text[:240]}")
    if search.isError:
        raise SystemExit(f"[{label}] search_jobs failed")

    if job_hash:
        got = await session.call_tool("get_job", {"job_hash": job_hash})
        body = got.content[0].text if got.content else ""
        print(f"[{label}] get_job({job_hash[:12]}...) -> isError={got.isError} {body[:240]}")
        if got.isError:
            raise SystemExit(f"[{label}] get_job failed")

    if score:
        # Real model calls: three uncached ones, plus the same three for the US
        # variant. Off by default for that reason, but it is the only way to see
        # that the fenced, hierarchy-declared prompt still parses and that the
        # cap actually moves a tier on live data.
        for location in ("Dublin, Ireland", "San Francisco, California"):
            res = await session.call_tool(
                "score_job",
                {"jd_text": SCORE_JD, "title": "Senior SRE", "company": "Acme",
                 "location": location},
            )
            text = res.content[0].text if res.content else ""
            print(f"[{label}] score_job({location}) -> isError={res.isError} {text}")
            if res.isError:
                raise SystemExit(f"[{label}] score_job failed")


async def over_stdio(job_hash: str | None, score: bool = False) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(REPO),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await exercise(session, "stdio", job_hash, score)


async def over_sse(job_hash: str | None, score: bool = False) -> None:
    import uvicorn
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    import app as app_module

    port = a_free_port()
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        url = f"http://127.0.0.1:{port}/mcp/sse"
        headers = {"Authorization": f"Bearer {mint_jwt()}"}
        print(f"[sse] connecting to {url} with a minted HS256 JWT")
        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await exercise(session, "sse", job_hash, score)
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await task


def first_job_hash() -> str | None:
    """A real job_hash for this user, so get_job returns a row and not None."""
    try:
        import httpx

        url = os.environ["SUPABASE_URL"].rstrip("/")
        key = os.environ["SUPABASE_SERVICE_KEY"]
        r = httpx.get(
            f"{url}/rest/v1/jobs?select=job_hash&user_id=eq.{USER_ID}&order=match_score.desc&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=20,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0]["job_hash"] if rows else None
    except Exception as exc:  # a missing sample is a weaker check, not a failure
        print(f"[warn] could not pick a real job_hash ({exc}) — skipping get_job")
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", help="only the stdio transport")
    parser.add_argument("--sse", action="store_true", help="only the HTTP/SSE transport")
    parser.add_argument("--job-hash", help="job_hash to fetch (default: this user's top match)")
    parser.add_argument("--score", action="store_true",
                        help="also call score_job (spends real, uncached model calls)")
    args = parser.parse_args()

    load_env()
    run_stdio = args.stdio or not args.sse
    run_sse = args.sse or not args.stdio
    if run_stdio:
        require_stdio_user()
    job_hash = args.job_hash or first_job_hash()

    if run_stdio:
        asyncio.run(over_stdio(job_hash, args.score))
    if run_sse:
        asyncio.run(over_sse(job_hash, args.score))
    print("OK — every requested transport listed 3 tools and completed a tool call")


if __name__ == "__main__":
    main()
