"""Preview-response cache backed by the Supabase `ai_cache` table.

Spec reference: docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md §7.7

Cache key format: apply_preview:{job_id}:{resume_version}
TTL: 10 minutes (per-entry expires_at)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def build_cache_key(job_id: str, resume_version: int) -> str:
    return f"apply_preview:{job_id}:{resume_version}"


def get_preview_cache(db, job_id: str, resume_version: int) -> Optional[dict]:
    """Return cached preview payload or None on miss/expired.

    Supabase returns JSONB columns as either parsed dicts OR serialized JSON
    strings depending on client version / column casting. We accept both so
    the caller always gets a dict. (Caught 2026-04-29: prod returned a string,
    causing TypeError: 'str' object does not support item assignment when
    callers tried `cached["cache_hit"] = True`.)
    """
    key = build_cache_key(job_id, resume_version)
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        db.table("ai_cache")
        .select("response")
        .eq("cache_key", key)
        .gte("expires_at", now)
        .execute()
    )
    if not resp.data:
        return None

    payload = resp.data[0]["response"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as e:
            # Stored payload corrupted — log and treat as miss rather than 500
            logger.warning(f"[preview_cache] corrupt cache for {key}: {e}")
            return None
    if not isinstance(payload, dict):
        # Unknown shape — treat as miss
        logger.warning(f"[preview_cache] unexpected cache shape for {key}: {type(payload).__name__}")
        return None
    return payload


def set_preview_cache(db, job_id: str, resume_version: int,
                      payload: dict, ttl_minutes: int = 10) -> None:
    """Write preview payload to cache with explicit TTL."""
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    db.table("ai_cache").upsert({
        "cache_key": build_cache_key(job_id, resume_version),
        "provider": "apply_preview",
        "model": "n/a",
        "response": payload,
        "expires_at": expires_at,
    }, on_conflict="cache_key").execute()
