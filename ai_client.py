"""
Multi-provider AI client with rate limiting, caching, and automatic failover.

Supported providers (all have free tiers):
  1. Groq (Llama 3.3 70B)     — 30 RPM, 14,400 RPD (primary, fastest)
  2. DeepSeek (DeepSeek V3)    — 60 RPM, ~500K tokens/day (strong structured output)
  3. OpenRouter (free models)  — Qwen 2.5 72B, Llama 3.3, etc. (aggregator failover)
  4. Anthropic Claude          — paid, used as premium fallback only

The client tries providers in order and fails over automatically.
Responses are cached in SQLite to avoid burning quota on repeated requests.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import sqlite3
import time
import threading
from dataclasses import dataclass, field
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

    def __init__(self, api_key: str, model: str = "google/gemini-2.0-flash-exp:free", **kwargs):
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


class DeepSeekProvider(AIProvider):
    """DeepSeek API — free tier, very capable for structured tasks.

    DeepSeek V3 is competitive with GPT-4o and Claude Sonnet on coding
    and structured output tasks. The API is OpenAI-compatible.
    Free tier: ~500K tokens/day, 60 RPM.
    Sign up: https://platform.deepseek.com/
    """

    def __init__(self, api_key: str, model: str = "deepseek-chat", **kwargs):
        super().__init__(
            name="deepseek",
            model=model,
            api_key=api_key,
            rate_limiter=RateLimiter(requests_per_minute=60, requests_per_day=5000),
            base_url="https://api.deepseek.com/v1",
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
            timeout=120,  # DeepSeek can be slower than Groq
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

    @property
    def stats(self) -> dict:
        return {**self._stats, "cache": self.cache.stats if self.cache else None,
                "providers": [f"{p.name}:{p.model}" for p in self.providers],
                "rate_limits": {p.name: p.rate_limiter.tokens_remaining for p in self.providers}}

    @classmethod
    def from_config(cls, config: dict) -> "AIClient":
        """Build client from config.yaml settings.

        Initializes providers based on available API keys.
        Priority order: Gemini → Groq → OpenRouter → Anthropic
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
        # → OpenRouter (many free models) → DeepSeek (if credits remain).
        # DeepSeek is accessed via NVIDIA NIM + OpenRouter too (free there).
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

        # 4. OpenRouter — free model aggregator (DeepSeek available here too)
        or_key = get_key("openrouter", "OPENROUTER_API_KEY")
        if or_key:
            or_models = [
                "meta-llama/llama-3.3-70b-instruct:free",
                "nousresearch/hermes-3-llama-3.1-405b:free",
                "nvidia/nemotron-3-super-120b-a12b:free",
                "mistralai/mistral-small-3.1-24b-instruct:free",
                "google/gemma-3-27b-it:free",
                "qwen/qwen3-coder:free",
                "stepfun/step-3.5-flash:free",
                "z-ai/glm-4.5-air:free",
            ]
            for model in or_models:
                providers.append(OpenRouterProvider(api_key=or_key, model=model))
            logger.info(f"[AI] OpenRouter council: {len(or_models)} free models")

        # 5. DeepSeek direct API — last resort (may have expired credits)
        ds_key = get_key("deepseek", "DEEPSEEK_API_KEY")
        if ds_key:
            providers.append(DeepSeekProvider(api_key=ds_key, model="deepseek-chat"))
            logger.info("[AI] DeepSeek direct: deepseek-chat (if credits remain)")

        if not providers:
            raise ProviderError(
                "No AI providers configured. Set at least one API key:\n"
                "  GROQ_API_KEY       — https://console.groq.com/keys (free, recommended)\n"
                "  OPENROUTER_API_KEY — https://openrouter.ai/keys (free models)\n"
                "  DEEPSEEK_API_KEY   — https://platform.deepseek.com/ (free)\n"
                "  ANTHROPIC_API_KEY  — https://console.anthropic.com/ (paid)"
            )

        logger.info(f"[AI] Total providers in council: {len(providers)}")

        # Cache setup
        cache_cfg = ai_cfg.get("cache", {})
        cache_ttl = cache_cfg.get("ttl_hours", 72)
        cache_path = cache_cfg.get("path", "output/.ai_cache.db")
        cache = ResponseCache(db_path=cache_path, ttl_hours=cache_ttl)

        return cls(providers=providers, cache=cache)
