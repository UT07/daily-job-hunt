"""PostHog-backed feature flags with fail-closed semantics.

The same PostHog client that captures events in app.py also evaluates
flags. We don't reinitialize it here — `set_client()` is called once
at app startup with the live client (or None in test/local-dev mode
without a key).

Contract:
- `is_enabled(flag, user_id, default=False)` never raises. Network
  errors fall back to `default`.
- `@flag_gated(flag)` wraps a FastAPI handler; returns HTTP 503 when
  the flag is off so the client gets a clean signal rather than
  silent disablement.
- Tests stub the client via `set_client()` — no real PostHog calls
  during unit tests.
"""
from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class _PostHogLike(Protocol):
    def feature_enabled(
        self, flag_key: str, distinct_id: str, **kwargs: Any
    ) -> Optional[bool]: ...


_client: Optional[_PostHogLike] = None


def set_client(client: Optional[_PostHogLike]) -> None:
    """Wire the live PostHog client (called once at startup)."""
    global _client
    _client = client


def is_enabled(flag: str, user_id: Optional[str], default: bool = False) -> bool:
    """Return whether a flag is on for this user.

    Args:
        flag: The flag key (e.g. 'auto_apply').
        user_id: Distinct ID — typically Supabase user UUID. None
            means anonymous; we never enable flags for anonymous
            users (returns `default`).
        default: Returned when client unavailable, user is None, or
            PostHog throws.
    """
    # Test-mode override: FEATURE_FLAGS_FORCE=auto_apply,council_scoring
    forced = os.environ.get("FEATURE_FLAGS_FORCE", "")
    if forced and flag in {f.strip() for f in forced.split(",") if f.strip()}:
        return True

    if _client is None or user_id is None:
        return default
    try:
        result = _client.feature_enabled(flag, user_id)
        # PostHog returns True/False/None (None = unknown flag → fail closed)
        return bool(result) if result is not None else default
    except Exception as exc:  # noqa: BLE001 — analytics must never break users
        logger.warning("feature_flag_eval_failed", extra={"flag": flag, "error": str(exc)})
        return default


def flag_gated(flag: str, default: bool = False):
    """Decorator: return 503 if flag is off for the caller.

    Usage:
        @app.post("/api/apply/start")
        @flag_gated("auto_apply")
        def start_apply(..., user: AuthUser = Depends(...)):
            ...

    The decorator looks for `user` in kwargs (FastAPI DI pattern); if
    absent or `user.id` is missing, behaves as if user_id=None
    (returns `default`).
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = kwargs.get("user")
            user_id = getattr(user, "id", None) if user else None
            if not is_enabled(flag, user_id, default=default):
                # Lazy import so the module doesn't require FastAPI
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=503,
                    detail=f"Feature '{flag}' is not enabled for your account.",
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator
