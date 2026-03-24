"""Audit trail middleware -- logs every authenticated API request.

Records: user_id, action (method + path), resource type, IP address,
user agent, timestamp.  Writes to the audit_log table in Supabase.
"""

import logging
import os
from typing import Optional

from fastapi import Request
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Module-level DB reference, set by app.py after startup
_db_ref = None

# Paths that should never be audited (health checks, docs, static)
_SKIP_PATHS = frozenset(("/api/health", "/docs", "/openapi.json", "/api/templates"))


def set_db(db) -> None:
    """Set the Supabase client for audit logging (called from app.py startup)."""
    global _db_ref
    _db_ref = db


class AuditMiddleware(BaseHTTPMiddleware):
    """Log all authenticated requests to the audit_log table."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only audit authenticated requests; skip health checks and docs
        if request.url.path in _SKIP_PATHS:
            return response

        # Try to extract user_id from request state (set by auth dependency)
        user_id: Optional[str] = getattr(request.state, "user_id", None)

        if not user_id:
            # Fallback: decode the JWT from the Authorization header
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
                    if secret:
                        payload = jwt.decode(
                            auth_header.split(" ", 1)[1],
                            secret,
                            algorithms=["HS256"],
                            audience="authenticated",
                        )
                        user_id = payload.get("sub")
                except Exception:
                    pass  # Unauthenticated or invalid token -- skip audit

        if user_id and _db_ref:
            try:
                action = f"{request.method} {request.url.path}"
                _db_ref.client.table("audit_log").insert({
                    "user_id": user_id,
                    "action": action,
                    "resource_type": _extract_resource_type(request.url.path),
                    "ip_address": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent", "")[:500],
                }).execute()
            except Exception as e:
                logger.warning("[AUDIT] Failed to log: %s", e)

        return response


def _extract_resource_type(path: str) -> str:
    """Extract resource type from API path.

    Examples:
        /api/dashboard/jobs   -> "job"
        /api/profile          -> "profile"
        /api/gdpr/export      -> "gdpr"
        /api/score            -> "score"
    """
    parts = path.strip("/").split("/")
    # parts[0] is "api", parts[1] is the resource group
    if len(parts) >= 2:
        resource = parts[1]  # "dashboard", "profile", "gdpr", etc.
        if resource == "dashboard" and len(parts) >= 3:
            return parts[2].rstrip("s")  # "jobs" -> "job"
        return resource
    return "unknown"
