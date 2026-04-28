"""Preview-response cache backed by the Supabase `ai_cache` table.

Spec reference: docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md §7.7

Cache key format: apply_preview:{job_id}:{resume_version}
TTL: 10 minutes (per-entry expires_at)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional


def build_cache_key(job_id: str, resume_version: int) -> str:
    return f"apply_preview:{job_id}:{resume_version}"


def get_preview_cache(db, job_id: str, resume_version: int) -> Optional[dict]:
    """Return cached preview payload or None on miss/expired."""
    key = build_cache_key(job_id, resume_version)
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        db.table("ai_cache")
        .select("response")
        .eq("cache_key", key)
        .gte("expires_at", now)
        .execute()
    )
    if resp.data:
        return resp.data[0]["response"]
    return None


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
