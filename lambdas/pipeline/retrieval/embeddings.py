"""Gemini text-embedding-004 client with content-addressed caching.

Reuses the generativelanguage REST pattern already established by
GeminiProvider in ai_client.py — same endpoint family, same auth, no SDK.
Embeddings cannot run in-process: sentence-transformers pulls ~800MB of torch,
which does not fit a Lambda package.

Import resolution for `ai_helper` mirrors lambdas/pipeline/agents/_ai_helper.py:
this package (lambdas/pipeline/retrieval/) lives inside the pipeline Lambdas'
own CodeUri (template.yaml: CodeUri: lambdas/pipeline/), which SAM/CFN
flattens into /var/task at deploy time. That makes `ai_helper` a flat sibling
module there — identical to the shape tests/conftest.py creates by putting
lambdas/pipeline on sys.path. The container-image Lambda (Dockerfile.lambda)
instead ships the whole lambdas/ package tree, where only the qualified
`lambdas.pipeline` import resolves. Try the flat import first since it covers
both tests and the deployed zip Lambda; fall back to the qualified import only
for the container-image shape. Never spell this as
`from lambdas.pipeline.ai_helper import ...` — that form cannot resolve once
CodeUri flattens this directory in the zip-based pipeline Lambdas.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

try:
    import ai_helper  # flat import — resolves under pytest and in the zip Lambda
except ImportError:
    from lambdas.pipeline import ai_helper  # container-image shape only

logger = logging.getLogger()

EMBED_DIM = 768
MODEL = "models/text-embedding-004"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/{MODEL}:embedContent"
BATCH_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/{MODEL}:batchEmbedContents"

# ai_cache.expires_at is TIMESTAMPTZ NOT NULL with no DB-level default (unlike
# created_at, which defaults to now()) — see
# db/migrations/003_phase2e_tables.sql. An embedding is a deterministic
# function of (model, text) and the cache key already hashes the text, so
# there's no freshness reason to expire it early the way ai_complete_cached's
# 72h AI-response cache or shared/preview_cache's 10-minute apply-preview
# cache do. A long fixed TTL satisfies the NOT NULL constraint without
# pretending this cache needs short-lived semantics.
_CACHE_TTL = timedelta(days=365)


def _api_key() -> str:
    return ai_helper.get_param("/naukribaba/GEMINI_API_KEY")


def cache_key(text: str) -> str:
    return "embed:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[float] | None:
    try:
        row = (
            ai_helper.get_supabase()
            .table("ai_cache")
            .select("response")
            .eq("cache_key", key)
            .gte("expires_at", datetime.now(timezone.utc).isoformat())
            .limit(1)
            .execute()
        )
        if row.data:
            return json.loads(row.data[0]["response"])
    except Exception as exc:
        logger.warning("[embed] cache read failed: %s", exc)
    return None


def _cache_put(key: str, vector: list[float]) -> None:
    try:
        expires_at = (datetime.now(timezone.utc) + _CACHE_TTL).isoformat()
        ai_helper.get_supabase().table("ai_cache").upsert(
            {
                "cache_key": key,
                "response": json.dumps(vector),
                "provider": "gemini",
                "model": MODEL,
                "expires_at": expires_at,
            },
            on_conflict="cache_key",
        ).execute()
    except Exception as exc:
        logger.warning("[embed] cache write failed: %s", exc)


def _check_dim(vector: list[float]) -> list[float]:
    if len(vector) != EMBED_DIM:
        raise ValueError(
            f"Embedding dimension {len(vector)} != {EMBED_DIM}. "
            "A model change would silently corrupt the HNSW index."
        )
    return vector


def embed(text: str) -> list[float]:
    """Embed one string. Cached by content hash."""
    key = cache_key(text)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    resp = httpx.post(
        ENDPOINT,
        params={"key": _api_key()},
        json={"model": MODEL, "content": {"parts": [{"text": text}]}},
        timeout=30,
    )
    resp.raise_for_status()
    vector = _check_dim(resp.json()["embedding"]["values"])
    _cache_put(key, vector)
    return vector


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed many strings. Cache hits are served without touching the network."""
    results: list[list[float] | None] = [_cache_get(cache_key(t)) for t in texts]
    missing = [i for i, r in enumerate(results) if r is None]
    if not missing:
        return results  # type: ignore[return-value]

    resp = httpx.post(
        BATCH_ENDPOINT,
        params={"key": _api_key()},
        json={"requests": [
            {"model": MODEL, "content": {"parts": [{"text": texts[i]}]}}
            for i in missing
        ]},
        timeout=60,
    )
    resp.raise_for_status()
    for slot, item in zip(missing, resp.json()["embeddings"]):
        vector = _check_dim(item["values"])
        results[slot] = vector
        _cache_put(cache_key(texts[slot]), vector)
    return results  # type: ignore[return-value]
