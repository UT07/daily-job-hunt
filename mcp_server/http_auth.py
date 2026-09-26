"""ASGI gate that requires a valid Supabase JWT before reaching the mounted
MCP transport.

Why this exists: `app.mount("/mcp", sub_app)` hands Starlette a bare ASGI
sub-application. `Depends(get_current_user)` — the mechanism every other
`/api/*` route in app.py uses for auth — is wired through FastAPI's route-
level dependency injection, which never runs for a `Mount`: from the parent
router's point of view, a mounted app is opaque ASGI, not a FastAPI route.
`JobHuntApi` is internet-facing (template.yaml notes it "has no
authentication" at the API-Gateway layer — auth is enforced entirely in
app.py's own route dependencies), so mounting the MCP transport without an
equivalent gate would expose `search_jobs` / `score_job` — someone's private
job-hunt data and resume — to any unauthenticated caller. Wrapping the
sub-app in a plain ASGI callable is the level at which a mount CAN be
gated, and it covers every route the sub-app defines today (`/sse`,
`/messages`) without needing to track FastMCP's internal routes as they
evolve.

Reuses `auth.get_current_user` — the exact HS256/ES256 Supabase verification
already serving every other endpoint — instead of a second implementation
that could drift from it.
"""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.types import ASGIApp, Receive, Scope, Send

from auth import get_current_user

logger = logging.getLogger(__name__)


class RequireSupabaseJWT:
    """Wrap an ASGI app so every HTTP request must carry a valid Supabase
    bearer token.

    Non-HTTP scopes (`lifespan`, `websocket`) pass straight through: there
    is nothing to authenticate about a lifespan event, and this transport
    (SSE-over-HTTP) never opens a websocket.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        token = _bearer_token(scope)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None

        try:
            get_current_user(credentials=credentials)
        except HTTPException as exc:
            logger.warning("[mcp-auth] rejected %s %s: %s", scope.get("method"), scope.get("path"), exc.detail)
            await _send_json_error(send, exc.status_code, exc.detail)
            return
        except Exception as exc:  # fail closed on anything unexpected, never fall through to the app
            logger.warning("[mcp-auth] auth check errored, rejecting: %s", exc)
            await _send_json_error(send, 401, "Invalid or expired token")
            return

        await self._app(scope, receive, send)


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1")
            if text.lower().startswith("bearer "):
                return text[len("bearer "):].strip()
    return None


async def _send_json_error(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
