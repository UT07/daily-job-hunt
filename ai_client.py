"""
Multi-provider AI client with rate limiting, caching, and automatic failover.

Supports two modes:
  1. **Failover chain** (`complete()`) — tries providers in order, fast and cheap.
  2. **Consensus council** (`council_complete()`) — sends the same prompt to N
     distinct models, has M other models critique/score the outputs, and returns
     the highest-rated response.  Produces measurably better quality at the cost
     of more API calls.

Supported providers (all have free tiers):
  1. Groq (Llama 3.3 70B + others) — 30 RPM, 14,400 RPD (fastest)
  2. OpenRouter (free models)       — aggregator with many free models
  3. NVIDIA NIM                     — free credits, DeepSeek/Kimi/Qwen/Mistral
  4. Qwen (DashScope)               — free tier, strong quality
  5. Anthropic Claude               — paid, premium fallback only

The client tries providers in order and fails over automatically.
Responses are cached in SQLite to avoid burning quota on repeated requests.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Retryable HTTP status codes for transient server-side errors
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# Max retry attempts per provider (so up to MAX_RETRIES+1 total attempts per provider)
_MAX_RETRIES = 2
# Initial backoff in seconds; doubles each retry
_BACKOFF_BASE = 2.0


# ── Rate Limiter (Token Bucket) ──────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter. Thread-safe."""

    def __init__(self, requests_per_minute: int, requests_per_day: int = 0):
        self.rpm = requests_per_minute
        self.rpd = requests_per_day
        self._lock = threading.Lock()

        # Minute bucket
        self._minute_tokens = float(requests_per_minute)
        self._minute_max = float(requests_per_minute)
        self._minute_refill_rate = requests_per_minute / 60.0  # tokens per second

        # Daily bucket (0 = unlimited)
        self._day_tokens = float(requests_per_day) if requests_per_day else float("inf")
        self._day_max = float(requests_per_day) if requests_per_day else float("inf")

        self._last_refill = time.monotonic()
        self._day_start = time.time()

    def acquire(self, timeout: float = 120.0) -> bool:
        """Block until a token is available, or return False on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                self._refill()
                if self._minute_tokens >= 1.0 and self._day_tokens >= 1.0:
                    self._minute_tokens -= 1.0
                    self._day_tokens -= 1.0
                    return True
            # Wait before retrying (adaptive sleep)
            wait = min(1.0 / max(self._minute_refill_rate, 0.1), 5.0)
            time.sleep(wait)
        return False

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._minute_tokens = min(self._minute_max, self._minute_tokens + elapsed * self._minute_refill_rate)
        self._last_refill = now

        # Reset daily counter at midnight
        if time.time() - self._day_start > 86400:
            self._day_tokens = self._day_max
            self._day_start = time.time()

    @property
    def tokens_remaining(self) -> dict:
        with self._lock:
            self._refill()
            return {
                "minute": round(self._minute_tokens, 1),
                "day": round(self._day_tokens, 1) if self._day_tokens != float("inf") else "unlimited",
            }


# ── Response Cache (SQLite) ──────────────────────────────────────────────

class ResponseCache:
    """SQLite-backed LLM response cache. Avoids re-processing identical prompts."""

    def __init__(self, db_path: str = "output/.ai_cache.db", ttl_hours: int = 72):
        # Lambda only allows writes to /tmp
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") and not db_path.startswith("/tmp"):
            db_path = f"/tmp/{Path(db_path).name}"
        self.db_path = db_path
        self.ttl_seconds = ttl_hours * 3600
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                created_at REAL NOT NULL
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON cache(created_at)")
        self._conn.commit()
        self._cleanup()

    def _make_key(self, prompt: str, system: str = "", cache_extra: str = "") -> str:
        raw = f"{system}|||{prompt}|||{cache_extra}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, prompt: str, system: str = "", cache_extra: str = "") -> Optional[str]:
        key = self._make_key(prompt, system, cache_extra)
        cutoff = time.time() - self.ttl_seconds
        row = self._conn.execute(
            "SELECT response FROM cache WHERE key = ? AND created_at > ?",
            (key, cutoff),
        ).fetchone()
        if row:
            return row[0]
        return None

    def get_with_info(self, prompt: str, system: str = "", cache_extra: str = "") -> Optional[dict]:
        """Like get() but returns provider/model info alongside the response.

        Returns: {"response": str, "provider": str, "model": str} or None.
        """
        key = self._make_key(prompt, system, cache_extra)
        cutoff = time.time() - self.ttl_seconds
        row = self._conn.execute(
            "SELECT response, provider, model FROM cache WHERE key = ? AND created_at > ?",
            (key, cutoff),
        ).fetchone()
        if row:
            return {"response": row[0], "provider": row[1] or "", "model": row[2] or ""}
        return None

    def put(self, prompt: str, response: str, provider: str = "", model: str = "", system: str = "", cache_extra: str = ""):
        key = self._make_key(prompt, system, cache_extra)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, response, provider, model, created_at) VALUES (?, ?, ?, ?, ?)",
            (key, response, provider, model, time.time()),
        )
        self._conn.commit()

    def _cleanup(self):
        cutoff = time.time() - self.ttl_seconds
        self._conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
        self._conn.commit()

    @property
    def stats(self) -> dict:
        row = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        return {"entries": row[0], "db_path": self.db_path}


# ── Provider Implementations ─────────────────────────────────────────────

@dataclass
class AIProvider:
    name: str
    model: str
    api_key: str
    rate_limiter: RateLimiter
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        raise NotImplementedError

    def complete_with_retry(self, prompt: str, system: str = "", temperature: float = None) -> str:
        """Call complete() with exponential backoff retry for transient errors.

        Retries up to _MAX_RETRIES times (so up to _MAX_RETRIES+1 total attempts)
        for HTTP 429/5xx errors and RateLimitError. Non-retryable errors (4xx other
        than 429, JSON errors, etc.) propagate immediately.
        """
        import requests as _requests

        last_error = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self.complete(prompt, system=system, temperature=temperature)
            except RateLimitError as e:
                # 429 from rate limiter or provider — retryable
                last_error = e
            except _requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in _RETRYABLE_STATUS_CODES:
                    last_error = e
                else:
                    # Non-retryable HTTP error (400, 401, 403, 404, …)
                    raise
            except (_requests.exceptions.ConnectionError,
                    _requests.exceptions.Timeout) as e:
                # Connection-level transient errors
                last_error = e
            except Exception as e:
                # Anything else (JSON parse error, SDK exception, etc.) — don't retry
                raise

            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE * (2 ** attempt)  # 2s, 4s
                logger.warning(f"[AI] {self.name} transient error ({last_error}); retrying in {wait:.0f}s "
                               f"(attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(wait)

        # All retries exhausted — re-raise the last error to trigger failover
        raise last_error


class GeminiProvider(AIProvider):
    """Google Gemini via the generativelanguage REST API (no SDK needed)."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", **kwargs):
        super().__init__(
            name="gemini",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=15, requests_per_day=1500),
            base_url="https://generativelanguage.googleapis.com/v1beta",
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import requests

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"[System instructions]\n{system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I'll follow these instructions."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": self.max_tokens,
            },
        }

        resp = requests.post(url, json=body, timeout=90)

        if resp.status_code == 429:
            raise RateLimitError(f"[{self.name}] HTTP 429 — rate limited")
        resp.raise_for_status()

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderError(f"[{self.name}] No candidates in response")

        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)


class GroqProvider(AIProvider):
    """Groq cloud inference (Llama 3.3 70B and others). OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", **kwargs):
        super().__init__(
            name="groq",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=30, requests_per_day=14400),
            base_url="https://api.groq.com/openai/v1",
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import requests

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages, "temperature": temp, "max_tokens": self.max_tokens},
            timeout=90,
        )

        if resp.status_code == 429:
            raise RateLimitError(f"[{self.name}] HTTP 429 — rate limited")
        resp.raise_for_status()

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class OpenRouterProvider(AIProvider):
    """OpenRouter — aggregates many free and paid models."""

    # Good free models on OpenRouter:
    #   "google/gemini-2.0-flash-exp:free"
    #   "meta-llama/llama-3.3-70b-instruct:free"
    #   "qwen/qwen-2.5-72b-instruct:free"

    def __init__(self, api_key: str, model: str = "nvidia/nemotron-3-super-120b-a12b:free", **kwargs):
        super().__init__(
            name="openrouter",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=20, requests_per_day=200),
            base_url="https://openrouter.ai/api/v1",
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import requests

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/UT07/daily-job-hunt",
            },
            json={"model": self.model, "messages": messages, "temperature": temp, "max_tokens": self.max_tokens},
            timeout=90,
        )

        if resp.status_code == 429:
            raise RateLimitError(f"[{self.name}] HTTP 429 — rate limited")
        resp.raise_for_status()

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class NvidiaNIMProvider(AIProvider):
    """NVIDIA NIM — free API access to top open models. OpenAI-compatible."""

    def __init__(self, api_key: str, model: str = "meta/llama-3.3-70b-instruct", **kwargs):
        super().__init__(
            name="nvidia",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=20, requests_per_day=5000),
            base_url="https://integrate.api.nvidia.com/v1",
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import requests

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages, "temperature": temp, "max_tokens": self.max_tokens},
            timeout=120,
        )

        if resp.status_code == 429:
            raise RateLimitError(f"[{self.name}] HTTP 429 — rate limited")
        resp.raise_for_status()

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class QwenProvider(AIProvider):
    """Alibaba Qwen via DashScope API (OpenAI-compatible). Free tier available."""

    def __init__(self, api_key: str, model: str = "qwen-plus", **kwargs):
        super().__init__(
            name="qwen",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=30, requests_per_day=5000),
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import requests

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages, "temperature": temp, "max_tokens": self.max_tokens},
            timeout=120,
        )

        if resp.status_code == 429:
            raise RateLimitError(f"[{self.name}] HTTP 429 — rate limited")
        resp.raise_for_status()

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class AnthropicProvider(AIProvider):
    """Anthropic Claude — paid fallback."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514", **kwargs):
        super().__init__(
            name="anthropic",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=50, requests_per_day=100000),
            **kwargs,
        )

    def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        import anthropic

        if not self.rate_limiter.acquire():
            raise RateLimitError(f"[{self.name}] Rate limit exceeded")

        temp = temperature if temperature is not None else self.temperature
        client = anthropic.Anthropic(api_key=self.api_key)

        kwargs = {"model": self.model, "max_tokens": self.max_tokens, "temperature": temp,
                  "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system

        resp = client.messages.create(**kwargs)
        return resp.content[0].text


# ── Errors ───────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    pass

class ProviderError(Exception):
    pass


# ── Main Client (Failover + Cache) ──────────────────────────────────────

class AIClient:
    """Multi-provider AI client with automatic failover and caching.

    Usage:
        client = AIClient.from_config(config)
        response = client.complete("Analyze this job posting...", system="You are a job matcher.")
    """

    # Permanent error codes — provider is broken, don't retry this session
    _DEAD_CODES = {401, 402, 403, 404}

    def __init__(self, providers: List[AIProvider], cache: Optional[ResponseCache] = None):
        self.providers = providers
        self.cache = cache or ResponseCache()
        self._stats = {"cache_hits": 0, "cache_misses": 0, "provider_calls": {}, "failovers": 0}
        # Track dead providers (permanent errors) — skip them in future calls
        self._dead_providers: set = set()  # Set of (provider.name, provider.model)

    def complete(self, prompt: str, system: str = "", temperature: float = None,
                 skip_cache: bool = False, cache_extra: str = "") -> str:
        """Send a prompt through the provider chain with caching and failover.

        Args:
            prompt: The user prompt.
            system: Optional system message.
            temperature: Override the provider's default temperature.
            skip_cache: If True, bypass the cache for both read and write.
            cache_extra: Extra string hashed into the cache key (e.g. a resume
                content hash) so that logically different inputs that happen to
                produce the same prompt text are cached separately.
        """

        # Check cache first
        if not skip_cache and self.cache:
            cached = self.cache.get(prompt, system, cache_extra=cache_extra)
            if cached:
                self._stats["cache_hits"] += 1
                return cached

        self._stats["cache_misses"] += 1
        last_error = None
        import requests as _req

        alive_providers = [
            p for p in self.providers
            if (p.name, p.model) not in self._dead_providers
        ]
        if not alive_providers:
            # All dead — reset and try again (maybe rate limits have reset)
            logger.warning("[AI] All providers marked dead — resetting health status")
            self._dead_providers.clear()
            alive_providers = self.providers

        for i, provider in enumerate(alive_providers):
            try:
                if i > 0:
                    self._stats["failovers"] += 1
                    logger.info(f"[AI] Failing over to {provider.name} ({provider.model})")

                response = provider.complete_with_retry(prompt, system=system, temperature=temperature)

                # Cache the response
                if self.cache and not skip_cache:
                    self.cache.put(prompt, response, provider=provider.name,
                                   model=provider.model, system=system, cache_extra=cache_extra)

                self._stats["provider_calls"][provider.name] = self._stats["provider_calls"].get(provider.name, 0) + 1
                return response

            except RateLimitError as e:
                logger.warning(f"[AI] {e} — trying next provider...")
                last_error = e
                continue
            except _req.HTTPError as e:
                # Detect permanent failures (402 payment required, 404 model not found, etc.)
                status = e.response.status_code if hasattr(e, 'response') and e.response else 0
                if status in self._DEAD_CODES:
                    key = (provider.name, provider.model)
                    self._dead_providers.add(key)
                    logger.warning(f"[AI] {provider.name}:{provider.model} permanently failed ({status}) — removed from council")
                else:
                    logger.warning(f"[AI] {provider.name} error: {e} — trying next provider...")
                last_error = e
                continue
            except Exception as e:
                logger.warning(f"[AI] {provider.name} error: {e} — trying next provider...")
                last_error = e
                continue

        raise ProviderError(f"All providers exhausted. Last error: {last_error}")

    def complete_with_info(self, prompt: str, system: str = "", temperature: float = None,
                           skip_cache: bool = False, cache_extra: str = "") -> dict:
        """Like complete() but returns provider/model info alongside the response.

        Returns: {"response": str, "provider": str, "model": str}
        """

        # Check cache first (with provider/model info)
        if not skip_cache and self.cache:
            cached = self.cache.get_with_info(prompt, system, cache_extra=cache_extra)
            if cached:
                self._stats["cache_hits"] += 1
                return cached

        self._stats["cache_misses"] += 1
        last_error = None
        import requests as _req

        alive_providers = [
            p for p in self.providers
            if (p.name, p.model) not in self._dead_providers
        ]
        if not alive_providers:
            logger.warning("[AI] All providers marked dead — resetting health status")
            self._dead_providers.clear()
            alive_providers = self.providers

        for i, provider in enumerate(alive_providers):
            try:
                if i > 0:
                    self._stats["failovers"] += 1
                    logger.info(f"[AI] Failing over to {provider.name} ({provider.model})")

                response = provider.complete_with_retry(prompt, system=system, temperature=temperature)

                # Cache the response
                if self.cache and not skip_cache:
                    self.cache.put(prompt, response, provider=provider.name,
                                   model=provider.model, system=system, cache_extra=cache_extra)

                self._stats["provider_calls"][provider.name] = self._stats["provider_calls"].get(provider.name, 0) + 1
                return {"response": response, "provider": provider.name, "model": provider.model}

            except RateLimitError as e:
                logger.warning(f"[AI] {e} — trying next provider...")
                last_error = e
                continue
            except _req.HTTPError as e:
                status = e.response.status_code if hasattr(e, 'response') and e.response else 0
                if status in self._DEAD_CODES:
                    key = (provider.name, provider.model)
                    self._dead_providers.add(key)
                    logger.warning(f"[AI] {provider.name}:{provider.model} permanently failed ({status}) — removed from council")
                else:
                    logger.warning(f"[AI] {provider.name} error: {e} — trying next provider...")
                last_error = e
                continue
            except Exception as e:
                logger.warning(f"[AI] {provider.name} error: {e} — trying next provider...")
                last_error = e
                continue

        raise ProviderError(f"All providers exhausted. Last error: {last_error}")

    # ── Council Methods ──────────────────────────────────────────────────

    def _select_providers(self, n: int, exclude: set = None) -> List[AIProvider]:
        """Pick N distinct providers/models from the pool, preferring alive ones.

        Selection strategy:
        - Filters out dead providers and any in the *exclude* set.
        - Deduplicates by **underlying model family** so the council gets
          genuinely different viewpoints (e.g. won't pick the same Llama 3.3
          from both Groq and NVIDIA).
        - Prefers providers that still have rate-limit headroom.
        - Shuffles within each tier to avoid always picking the same subset.

        Args:
            n: Number of providers to select.
            exclude: Set of ``(provider.name, provider.model)`` tuples to skip
                (e.g. generators when picking critics).

        Returns:
            Up to *n* :class:`AIProvider` instances (may be fewer if the pool
            is too small).
        """
        exclude = exclude or set()

        # Filter out dead providers AND small-context models that can't handle resumes
        _SMALL_MODELS = {"llama-3.1-8b-instant", "llama-4-scout-17b-16e-instruct",
                         "mistral-small-3.1-24b-instruct", "mistralai/mistral-small-3.1-24b-instruct-2503",
                         "mistralai/mistral-small-3.1-24b-instruct:free"}
        alive = [
            p for p in self.providers
            if (p.name, p.model) not in self._dead_providers
            and (p.name, p.model) not in exclude
            and p.model not in _SMALL_MODELS
        ]

        if not alive:
            logger.warning("[Council] No alive providers outside exclusion set — falling back to full pool")
            alive = [p for p in self.providers if (p.name, p.model) not in exclude]

        # Deduplicate by normalised model family so the council gets diverse models.
        def _model_family(model: str) -> str:
            """Collapse provider-prefixed model names to a canonical family."""
            m = model.lower().split("/")[-1]         # strip org prefix
            m = m.replace(":free", "")                # strip OpenRouter `:free` tag
            # Collapse version variants: "deepseek-v3.2" and "deepseek-chat" are the same family
            for prefix in ("deepseek", "llama-3.3", "llama-3.1", "llama-4", "qwen3",
                           "qwen-plus", "qwen-turbo", "qwen-max", "kimi-k2",
                           "mistral-small", "nemotron", "hermes", "gemma", "glm", "step"):
                if m.startswith(prefix):
                    return prefix
            return m

        seen_families: set = set()
        unique: List[AIProvider] = []
        # Shuffle so we don't always pick the first provider in a family
        shuffled = list(alive)
        random.shuffle(shuffled)

        # Sort so providers with more minute-tokens come first (more headroom)
        def _headroom(p: AIProvider) -> float:
            info = p.rate_limiter.tokens_remaining
            minute_val = info.get("minute", 0)
            return float(minute_val) if isinstance(minute_val, (int, float)) else 0.0
        shuffled.sort(key=_headroom, reverse=True)

        for p in shuffled:
            fam = _model_family(p.model)
            if fam not in seen_families:
                seen_families.add(fam)
                unique.append(p)
                if len(unique) >= n:
                    break

        return unique

    def council_generate(
        self,
        prompt: str,
        system: str = "",
        n_generators: int = 3,
        temperature: float = 0.3,
        skip_cache: bool = False,
        cache_extra: str = "",
    ) -> List[Dict[str, Any]]:
        """Send the same prompt to N distinct models and collect their responses.

        Returns a list of dicts, each containing ``response``, ``provider``,
        and ``model`` keys.  If fewer than *n_generators* succeed the list may
        be shorter (even length 1 is acceptable).
        """
        generators = self._select_providers(n_generators)
        if not generators:
            raise ProviderError("[Council] No providers available for generation")

        logger.info(f"[Council] Generating with {len(generators)} models: "
                    f"{', '.join(f'{p.name}:{p.model}' for p in generators)}")

        results: List[Dict[str, Any]] = []
        import requests as _req
        import concurrent.futures

        def _generate_one(provider):
            """Generate from one provider with a hard timeout."""
            resp = provider.complete_with_retry(prompt, system=system, temperature=temperature)
            return {"response": resp, "provider": provider.name, "model": provider.model}

        # Run generators concurrently with a 60-second hard timeout per provider
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(generators)) as executor:
            future_to_provider = {
                executor.submit(_generate_one, p): p for p in generators
            }
            try:
                for future in concurrent.futures.as_completed(future_to_provider, timeout=120):
                    provider = future_to_provider[future]
                    try:
                        result = future.result(timeout=60)
                        results.append(result)
                        self._stats["provider_calls"][provider.name] = (
                            self._stats["provider_calls"].get(provider.name, 0) + 1
                        )
                        logger.info(f"[Council] {provider.name}:{provider.model} generated "
                                    f"{len(result['response'])} chars")
                    except concurrent.futures.TimeoutError:
                        logger.warning(f"[Council] {provider.name}:{provider.model} timed out (60s)")
                    except Exception as e:
                        logger.warning(f"[Council] {provider.name}:{provider.model} failed: {e}")
                        # Mark permanently dead providers (402, 403, etc.)
                        if isinstance(e, _req.HTTPError):
                            status = e.response.status_code if hasattr(e, "response") and e.response else 0
                            if status in self._DEAD_CODES:
                                self._dead_providers.add((provider.name, provider.model))
                                logger.warning(f"[Council] Marked {provider.name}:{provider.model} dead ({status})")
                        elif "Payment Required" in str(e) or "402" in str(e):
                            self._dead_providers.add((provider.name, provider.model))
                            logger.warning(f"[Council] Marked {provider.name}:{provider.model} dead (payment)")
                        continue
            except concurrent.futures.TimeoutError:
                # Outer timeout — keep whatever results we have so far
                logger.warning(f"[Council] Overall timeout (120s) — collected {len(results)} of {len(generators)} results")

        if not results:
            raise ProviderError("[Council] All generators failed — no candidates produced")

        return results

    def council_critique(
        self,
        candidates: List[Dict[str, Any]],
        task_description: str,
        n_critics: int = 2,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Have M critic models score the candidate responses and pick the best.

        Critics are chosen from providers that were NOT used for generation so
        that no model evaluates its own output.

        Returns a dict with ``best_response``, ``best_provider``, ``best_model``,
        ``best_score``, and ``critiques`` keys.
        """
        # Build exclusion set — generators should not critique their own work
        generator_keys = {(c["provider"], c["model"]) for c in candidates}
        critics = self._select_providers(n_critics, exclude=generator_keys)

        if not critics:
            logger.warning("[Council] No distinct critics available — using generators as fallback")
            critics = self._select_providers(n_critics)

        # Build the critique prompt
        candidate_blocks = []
        for i, cand in enumerate(candidates, 1):
            truncated = cand["response"][:3000]
            candidate_blocks.append(f"Candidate {i} ({cand['provider']}:{cand['model']}):\n{truncated}")

        critique_prompt = (
            f"You are evaluating {len(candidates)} candidate outputs for this task:\n"
            f"{task_description}\n\n"
            "Rate each candidate 0-100 on: accuracy, completeness, quality, and "
            "adherence to instructions. Average the four dimensions into a single "
            "score per candidate.\n\n"
            + "\n\n".join(candidate_blocks)
            + "\n\nReturn ONLY a JSON array of integer scores in candidate order, "
            "e.g. [85, 72, 91]. No other text."
        )

        logger.info(f"[Council] Critiquing {len(candidates)} candidates with "
                    f"{len(critics)} critics: "
                    f"{', '.join(f'{c.name}:{c.model}' for c in critics)}")

        all_critiques: List[Dict[str, Any]] = []
        # Accumulate scores: index -> list of scores from each critic
        score_accumulator: Dict[int, List[float]] = {i: [] for i in range(len(candidates))}

        for critic in critics:
            try:
                raw = critic.complete_with_retry(
                    critique_prompt,
                    system="You are an impartial AI output evaluator. Return only valid JSON.",
                    temperature=temperature,
                )
                self._stats["provider_calls"][critic.name] = (
                    self._stats["provider_calls"].get(critic.name, 0) + 1
                )

                # Parse scores from response — look for a JSON array
                scores = self._parse_scores(raw, len(candidates))
                if scores:
                    all_critiques.append({
                        "critic_provider": critic.name,
                        "critic_model": critic.model,
                        "scores": scores,
                    })
                    for idx, s in enumerate(scores):
                        score_accumulator[idx].append(float(s))
                    logger.info(f"[Council] Critic {critic.name}:{critic.model} scores: {scores}")
                else:
                    logger.warning(f"[Council] Could not parse scores from {critic.name}:{critic.model}: {raw[:200]}")

            except Exception as e:
                logger.warning(f"[Council] Critic {critic.name}:{critic.model} failed: {e}")
                continue

        # Compute average scores and pick the winner
        avg_scores: List[float] = []
        for idx in range(len(candidates)):
            values = score_accumulator[idx]
            avg_scores.append(sum(values) / len(values) if values else 0.0)

        if not any(avg_scores):
            # No critics returned usable scores — fall back to first candidate
            logger.warning("[Council] No usable critique scores — defaulting to first candidate")
            best_idx = 0
            best_score = 0.0
        else:
            best_idx = max(range(len(avg_scores)), key=lambda i: avg_scores[i])
            best_score = round(avg_scores[best_idx], 1)

        winner = candidates[best_idx]
        logger.info(f"[Council] Winner: {winner['provider']}:{winner['model']} "
                    f"(score {best_score}, avg scores: {[round(s, 1) for s in avg_scores]})")

        return {
            "best_response": winner["response"],
            "best_provider": winner["provider"],
            "best_model": winner["model"],
            "best_score": best_score,
            "critiques": all_critiques,
        }

    @staticmethod
    def _parse_scores(raw: str, expected_count: int) -> Optional[List[int]]:
        """Extract a JSON array of integer scores from a critic's response.

        Tries several strategies: direct JSON parse, regex extraction of
        bracket-delimited arrays, and fallback number extraction.
        """
        # Strategy 1: direct JSON parse
        try:
            parsed = json.loads(raw.strip())
            if isinstance(parsed, list) and len(parsed) == expected_count:
                return [int(round(float(s))) for s in parsed]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Strategy 2: find the first JSON array in the text
        match = re.search(r'\[[\d\s,\.]+\]', raw)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list) and len(parsed) == expected_count:
                    return [int(round(float(s))) for s in parsed]
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Strategy 3: extract all numbers and hope for the best
        numbers = re.findall(r'\b(\d{1,3})\b', raw)
        # Filter to plausible scores (0-100)
        plausible = [int(n) for n in numbers if 0 <= int(n) <= 100]
        if len(plausible) >= expected_count:
            return plausible[:expected_count]

        return None

    def council_complete(
        self,
        prompt: str,
        system: str = "",
        n_generators: int = 3,
        n_critics: int = 2,
        task_description: str = "",
        temperature: float = 0.3,
        skip_cache: bool = False,
        cache_extra: str = "",
    ) -> str:
        """Generate with multiple models, critique, and return the best response.

        This is the main entry point for high-quality generation. Combines
        :meth:`council_generate` and :meth:`council_critique` into a single call.

        Falls back gracefully:
        - If only 1 generator succeeds, skips critique and returns it directly.
        - Caches the final winning response (not intermediate candidates).
        """
        # Check cache first
        council_cache_extra = f"council:{n_generators}x{n_critics}|{cache_extra}"
        if not skip_cache and self.cache:
            cached = self.cache.get_with_info(prompt, system, cache_extra=council_cache_extra)
            if cached:
                self._stats["cache_hits"] += 1
                logger.debug("[Council] Cache hit — returning cached council result")
                self.last_council_provider = cached.get("provider", "council")
                self.last_council_model = cached.get("model", "consensus")
                return cached["response"]

        self._stats["cache_misses"] += 1

        # Generate candidates
        candidates = self.council_generate(
            prompt, system=system, n_generators=n_generators,
            temperature=temperature, skip_cache=True, cache_extra=cache_extra,
        )

        # If only 1 candidate, skip critique
        if len(candidates) == 1:
            logger.info("[Council] Only 1 candidate — skipping critique round")
            best = candidates[0]["response"]
            self.last_council_provider = candidates[0]["provider"]
            self.last_council_model = candidates[0]["model"]
            if self.cache and not skip_cache:
                self.cache.put(prompt, best, provider=candidates[0]["provider"],
                               model=candidates[0]["model"], system=system,
                               cache_extra=council_cache_extra)
            self._log_quality({
                "task": task_description or "council_complete",
                "generators": [{"provider": c["provider"], "model": c["model"]} for c in candidates],
                "critiques": [],
                "winner": {"provider": candidates[0]["provider"],
                           "model": candidates[0]["model"], "score": None},
            })
            return best

        # Critique and select
        desc = task_description or "Produce a high-quality response to the given prompt."
        result = self.council_critique(candidates, desc, n_critics=n_critics, temperature=0.2)

        best = result["best_response"]
        self.last_council_provider = result["best_provider"]
        self.last_council_model = result["best_model"]

        # Cache the winning response
        if self.cache and not skip_cache:
            self.cache.put(prompt, best, provider=result["best_provider"],
                           model=result["best_model"], system=system,
                           cache_extra=council_cache_extra)

        # Log quality data
        self._log_quality({
            "task": task_description or "council_complete",
            "generators": [{"provider": c["provider"], "model": c["model"]} for c in candidates],
            "critiques": result["critiques"],
            "winner": {"provider": result["best_provider"],
                       "model": result["best_model"],
                       "score": result["best_score"]},
        })

        return best

    def consensus_score(
        self,
        prompt: str,
        system: str = "",
        n_scorers: int = 3,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Send a scoring prompt to N models and average their numeric outputs.

        Designed for job matching — each scorer returns JSON with ``ats_score``,
        ``hiring_manager_score``, and ``tech_recruiter_score`` fields.  We average
        across scorers for a more robust evaluation.

        Returns a dict with averaged scores and the individual per-scorer breakdown.
        """
        scorers = self._select_providers(n_scorers)
        if not scorers:
            raise ProviderError("[Council] No providers available for scoring")

        logger.info(f"[Council] Scoring with {len(scorers)} models: "
                    f"{', '.join(f'{s.name}:{s.model}' for s in scorers)}")

        individual_scores: List[Dict[str, Any]] = []

        for scorer in scorers:
            try:
                raw = scorer.complete_with_retry(prompt, system=system, temperature=temperature)
                self._stats["provider_calls"][scorer.name] = (
                    self._stats["provider_calls"].get(scorer.name, 0) + 1
                )

                parsed = self._extract_scores_json(raw)
                if parsed:
                    individual_scores.append({
                        "provider": scorer.name,
                        "model": scorer.model,
                        "ats": parsed.get("ats_score", parsed.get("ats", 0)),
                        "hm": parsed.get("hiring_manager_score", parsed.get("hm", 0)),
                        "tr": parsed.get("tech_recruiter_score", parsed.get("tr", 0)),
                    })
                    logger.info(f"[Council] Scorer {scorer.name}:{scorer.model} → "
                                f"ATS={individual_scores[-1]['ats']}, "
                                f"HM={individual_scores[-1]['hm']}, "
                                f"TR={individual_scores[-1]['tr']}")
                else:
                    logger.warning(f"[Council] Could not parse scores from {scorer.name}:{scorer.model}")

            except Exception as e:
                logger.warning(f"[Council] Scorer {scorer.name}:{scorer.model} failed: {e}")
                continue

        if not individual_scores:
            raise ProviderError("[Council] All scorers failed — no scores produced")

        # Average across scorers
        n = len(individual_scores)
        avg_ats = round(sum(s["ats"] for s in individual_scores) / n)
        avg_hm = round(sum(s["hm"] for s in individual_scores) / n)
        avg_tr = round(sum(s["tr"] for s in individual_scores) / n)

        return {
            "ats_score": avg_ats,
            "hiring_manager_score": avg_hm,
            "tech_recruiter_score": avg_tr,
            "individual_scores": individual_scores,
        }

    @staticmethod
    def _extract_scores_json(raw: str) -> Optional[Dict[str, Any]]:
        """Extract a JSON object containing score fields from a model response.

        Handles markdown code fences, extra whitespace, and partial JSON.
        """
        # Strip markdown code fences
        cleaned = re.sub(r'```(?:json)?\s*', '', raw)
        cleaned = cleaned.strip().rstrip('`')

        # Try direct parse
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Find first JSON object in text
        match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        # Try nested JSON objects (the response might have nested braces)
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _log_quality(self, entry: Dict[str, Any]) -> None:
        """Append a quality log entry to ``output/ai_quality_log.jsonl``.

        Each line is a self-contained JSON object recording which models
        generated, which critiqued, and who won.  Useful for analysing
        provider quality over time.
        """
        log_path = Path("/tmp/ai_quality_log.jsonl") if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else Path("output/ai_quality_log.jsonl")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry["timestamp"] = datetime.now(timezone.utc).isoformat()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[Council] Failed to write quality log: {e}")

    @property
    def stats(self) -> dict:
        return {**self._stats, "cache": self.cache.stats if self.cache else None,
                "providers": [f"{p.name}:{p.model}" for p in self.providers],
                "rate_limits": {p.name: p.rate_limiter.tokens_remaining for p in self.providers}}

    @classmethod
    def from_config(cls, config: dict) -> "AIClient":
        """Build client from config.yaml settings.

        Initializes providers based on available API keys.
        Priority order: Qwen → Groq → NVIDIA NIM → OpenRouter
        (Gemini intentionally excluded — user reserves it for other purposes.
        DeepSeek removed — credits exhausted, accessible via NVIDIA NIM + OpenRouter.)
        """
        providers = []
        ai_cfg = config.get("ai", {})
        keys = config.get("api_keys", {})

        # Helper to resolve keys from config or env vars
        def get_key(name: str, env_var: str) -> str:
            val = keys.get(name, "")
            if not val or (val.startswith("${") and val.endswith("}")):
                val = os.environ.get(env_var, "")
            return val

        # ── LLM Council: all free models, ordered by preference ──
        # Strategy: Qwen (preferred) → Groq (fastest) → NVIDIA NIM (deep catalog)
        # → OpenRouter (many free models).
        # DeepSeek direct API removed (credits exhausted, 402 errors).
        # DeepSeek models still accessible via NVIDIA NIM + OpenRouter (free there).
        # No paid providers (Anthropic removed).

        # 1. Qwen (Alibaba DashScope — user preferred, free tier)
        qwen_key = get_key("qwen", "QWEN_API_KEY")
        if qwen_key:
            qwen_models = [
                "qwen-plus",          # Best quality
                "qwen-turbo",         # Fast
                "qwen-max",           # Largest
            ]
            for model in qwen_models:
                providers.append(QwenProvider(api_key=qwen_key, model=model))
            logger.info(f"[AI] Qwen council: {len(qwen_models)} models (preferred)")

        # 2. Groq — fastest inference, multiple free models
        groq_key = get_key("groq", "GROQ_API_KEY")
        if groq_key:
            groq_models = [
                "llama-3.3-70b-versatile",                     # Best overall, 128K
                "qwen/qwen3-32b",                              # Strong JSON
                "moonshotai/kimi-k2-instruct",                 # Kimi K2 on Groq
                "meta-llama/llama-4-scout-17b-16e-instruct",   # Fast
                "llama-3.1-8b-instant",                        # Lightweight fallback
            ]
            for model in groq_models:
                providers.append(GroqProvider(api_key=groq_key, model=model))
            logger.info(f"[AI] Groq council: {len(groq_models)} models")

        # 3. NVIDIA NIM — free credits, DeepSeek + Kimi + Qwen available here
        nvidia_key = get_key("nvidia", "NVIDIA_API_KEY")
        if nvidia_key:
            nvidia_models = [
                "deepseek-ai/deepseek-v3.2",                      # Top open model (free via NIM)
                "moonshotai/kimi-k2.5",                            # Kimi K2.5 (free via NIM)
                "qwen/qwen3.5-122b-a10b",                         # Large Qwen MoE
                "meta/llama-3.3-70b-instruct",                     # Solid general
                "nvidia/llama-3.3-nemotron-super-49b-v1.5",        # Strong structured output
                "mistralai/mistral-small-3.1-24b-instruct-2503",   # Fast
                "nvidia/nemotron-3-super-120b-a12b",               # NVIDIA flagship
            ]
            for model in nvidia_models:
                providers.append(NvidiaNIMProvider(api_key=nvidia_key, model=model))
            logger.info(f"[AI] NVIDIA NIM council: {len(nvidia_models)} models (incl. DeepSeek, Kimi)")

        # 4. OpenRouter — free model aggregator (includes Llama, GPT-OSS, NVIDIA, etc.)
        or_key = get_key("openrouter", "OPENROUTER_API_KEY")
        if or_key:
            # Verified-working free models on OpenRouter (2026-04-05).
            # Includes Meta Llama (rate-limited sometimes), NVIDIA Nemotron,
            # OpenAI GPT-OSS, Qwen, Minimax, Arcee, z-ai GLM.
            or_models = [
                # Llama family (Meta open-source)
                "meta-llama/llama-3.3-70b-instruct:free",
                "meta-llama/llama-3.2-3b-instruct:free",
                "nousresearch/hermes-3-llama-3.1-405b:free",
                # NVIDIA Nemotron
                "nvidia/nemotron-3-super-120b-a12b:free",
                "nvidia/nemotron-3-nano-30b-a3b:free",
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "nvidia/nemotron-nano-9b-v2:free",
                # OpenAI open-source
                "openai/gpt-oss-120b:free",
                "openai/gpt-oss-20b:free",
                # Qwen (very large context)
                "qwen/qwen3.6-plus:free",
                "qwen/qwen3-next-80b-a3b-instruct:free",
                "qwen/qwen3-coder:free",
                # Others
                "z-ai/glm-4.5-air:free",
                "google/gemma-3-27b-it:free",
                "minimax/minimax-m2.5:free",
                "arcee-ai/trinity-mini:free",
                "arcee-ai/trinity-large-preview:free",
            ]
            for model in or_models:
                providers.append(OpenRouterProvider(api_key=or_key, model=model))
            logger.info(f"[AI] OpenRouter council: {len(or_models)} free models")

        if not providers:
            raise ProviderError(
                "No AI providers configured. Set at least one API key:\n"
                "  GROQ_API_KEY       — https://console.groq.com/keys (free, recommended)\n"
                "  OPENROUTER_API_KEY — https://openrouter.ai/keys (free models)\n"
                "  NVIDIA_API_KEY     — https://build.nvidia.com (free credits)\n"
                "  QWEN_API_KEY       — https://dashscope.console.aliyun.com (free tier)"
            )

        logger.info(f"[AI] Total providers in council: {len(providers)}")

        # Cache setup
        cache_cfg = ai_cfg.get("cache", {})
        cache_ttl = cache_cfg.get("ttl_hours", 72)
        cache_path = cache_cfg.get("path", "output/.ai_cache.db")
        cache = ResponseCache(db_path=cache_path, ttl_hours=cache_ttl)

        return cls(providers=providers, cache=cache)
