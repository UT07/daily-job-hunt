"""Who an MCP tool call is acting for.

Every tool in `mcp_server.server` reads the caller from here and nowhere else.
There is deliberately no default: until 2026-10-08 every tool ran as a
hard-coded owner account, so any signed-up user holding a valid JWT read the
owner's jobs and spent the owner's LLM budget scoring against the owner's
résumé. A tool that cannot say who it is acting for now refuses.

How the identity gets set:

- HTTP (`/mcp` on the API Lambda): `mcp_server.http_auth.RequireSupabaseJWT`
  verifies the Supabase JWT and runs the mounted app inside
  `acting_as(<the token's sub>)`. The SSE session's task group is created
  inside that request, so tool calls handled by the session inherit it. The
  gate also sets `scope["user"]`, which makes the MCP SDK refuse a POST to a
  session from any other user (mcp/server/sse.py `_session_owners`), so the
  session's identity and the poster's identity cannot differ.
- stdio (`python -m mcp_server.server`): `mcp_server.server.main` requires the
  user id explicitly, in `NAUKRIBABA_MCP_USER_ID`, and refuses to start
  without it. See docs/runbooks/mcp-client-setup.md.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_caller: ContextVar[str | None] = ContextVar("naukribaba_mcp_caller", default=None)


class NoCallerIdentity(RuntimeError):
    """A tool ran with nobody to act for. Never answered with a default user."""


def current_user_id_or_none() -> str | None:
    return _caller.get()


def caller_user_id() -> str:
    user_id = _caller.get()
    if not user_id:
        raise NoCallerIdentity(
            "MCP tool called without an authenticated caller; refusing rather "
            "than acting as any default account"
        )
    return user_id


def set_caller(user_id: str):
    """Set the caller for the rest of this context. Returns the reset token."""
    if not user_id:
        raise NoCallerIdentity("refusing to set an empty caller identity")
    return _caller.set(user_id)


@contextmanager
def acting_as(user_id: str) -> Iterator[None]:
    token = set_caller(user_id)
    try:
        yield
    finally:
        _caller.reset(token)
