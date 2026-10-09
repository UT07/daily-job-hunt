"""Two waits that were longer than anything said they were.

1. `RateLimiter.acquire` blocks up to 120s and then the provider raises
   RateLimitError. `complete_with_retry` treated that as a transient upstream
   429 and retried it twice more, so a provider whose LOCAL bucket was empty
   cost 120 + 2 + 120 + 4 + 120 = 366s before failover. The bucket is ours;
   asking it again cannot produce a token faster than its refill rate, and
   another provider is the whole point of the chain. It now fails over at
   once. An upstream HTTP 429 is still retried — that one can clear.

2. The council's "120s hard timeout" was `as_completed(..., timeout=120)`
   inside `with ThreadPoolExecutor(...)`. The timeout fired on time, and then
   the `with` block's exit called `shutdown(wait=True)` and sat on the hung
   thread for as long as it took — up to the 90-120s HTTP timeout per
   provider, plus its retries. The bound bounded nothing.
"""
import sys
import threading
import time

import pytest

sys.path.insert(0, ".")
import ai_client  # noqa: E402
from ai_client import AIClient, GroqProvider, RateLimiter, ResponseCache  # noqa: E402


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(db_path=str(tmp_path / "cache.db"))


# -- 1. local rate-limit exhaustion -----------------------------------------

class _EmptyBucket(RateLimiter):
    """A bucket that is out of tokens. Returns at once instead of blocking
    120s, which is the only difference from the real one running dry."""

    def __init__(self):
        super().__init__(requests_per_minute=1)
        self.acquires = 0

    def acquire(self, timeout: float = 120.0) -> bool:
        self.acquires += 1
        return False


def test_the_double_reproduces_a_dry_bucket():
    """Prove the instrument: a real provider over this bucket raises the same
    RateLimitError it raises in production after the 120s block."""
    p = GroqProvider(api_key="k", model="m")
    p.rate_limiter = _EmptyBucket()
    with pytest.raises(ai_client.RateLimitError):
        p.complete("prompt")


def test_local_exhaustion_is_not_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ai_client.time, "sleep", lambda s: sleeps.append(s))
    p = GroqProvider(api_key="k", model="m")
    p.rate_limiter = bucket = _EmptyBucket()

    with pytest.raises(ai_client.RateLimitError):
        p.complete_with_retry("prompt")

    assert bucket.acquires == 1, f"local bucket asked {bucket.acquires} times — each is a 120s block"
    assert sleeps == [], f"backed off {sleeps} before failing over"


def test_an_upstream_429_is_still_retried(monkeypatch):
    """Control: only OUR bucket is excluded from retry."""
    import json

    import requests

    monkeypatch.setattr(ai_client.time, "sleep", lambda s: None)
    calls = []

    def post(*_a, **_kw):
        calls.append(1)
        r = requests.Response()
        r.status_code = 429
        r._content = json.dumps({"error": "slow down"}).encode()
        return r

    monkeypatch.setattr(requests, "post", post)
    p = GroqProvider(api_key="k", model="m")
    with pytest.raises(ai_client.RateLimitError):
        p.complete_with_retry("prompt")
    assert len(calls) == ai_client._MAX_RETRIES + 1


def test_client_fails_over_past_a_dry_bucket(monkeypatch, cache):
    import json as _json

    import requests

    monkeypatch.setattr(ai_client.time, "sleep", lambda s: None)

    def post(url, headers=None, json=None, timeout=None):  # noqa: A002
        r = requests.Response()
        r.status_code = 200
        r._content = _json.dumps(
            {"choices": [{"message": {"content": "from live"}, "finish_reason": "stop"}]}).encode()
        return r

    monkeypatch.setattr(requests, "post", post)
    dry = GroqProvider(api_key="k", model="dry")
    dry.rate_limiter = bucket = _EmptyBucket()
    client = AIClient([dry, GroqProvider(api_key="k", model="live")], cache=cache)

    assert client.complete("prompt", skip_cache=True) == "from live"
    assert bucket.acquires == 1


# -- 2. the council's timeout really bounds the call --------------------------

class _Limiter:
    tokens_remaining = {"minute": 100, "day": 100}


class _Stub:
    """Same shape as the council doubles that have been wrong before
    (CLAUDE.md #6): rate_limiter, complete_with_retry, positional prompt."""

    def __init__(self, name, model, text="answer", hang: threading.Event | None = None):
        self.name, self.model, self.text, self.hang = name, model, text, hang
        self.rate_limiter = _Limiter()
        self.started = threading.Event()

    def complete(self, *_a, **_kw):
        self.started.set()
        if self.hang is not None:
            self.hang.wait(10)          # a provider stuck on a slow socket
            return "too late"
        return self.text

    def complete_with_retry(self, *a, **kw):
        return self.complete(*a, **kw)


def test_the_hanging_double_really_hangs():
    release = threading.Event()
    s = _Stub("groq", "alpha-hangs", hang=release)
    t = threading.Thread(target=s.complete, daemon=True)
    t.start()
    assert s.started.wait(1)
    t.join(0.3)
    assert t.is_alive(), "the double must block, or the timing test proves nothing"
    release.set()


def test_council_timeout_bounds_the_whole_call(monkeypatch, cache):
    release = threading.Event()
    hung = _Stub("groq", "alpha-hangs", hang=release)
    fast = _Stub("groq", "beta-fast", text="fast answer")
    client = AIClient([hung, fast], cache=cache)
    monkeypatch.setattr(client, "_COUNCIL_TIMEOUT_S", 0.3)

    try:
        t0 = time.monotonic()
        results = client.council_generate("prompt", n_generators=2)
        elapsed = time.monotonic() - t0
    finally:
        release.set()

    assert hung.started.is_set(), "the hanging provider was never even called"
    assert [r["model"] for r in results] == ["beta-fast"]
    assert elapsed < 2.0, f"council_generate took {elapsed:.1f}s against a 0.3s timeout"


def test_council_retry_round_timeout_is_bounded_too(monkeypatch, cache):
    """The retry round had its own `with` block. First round: two providers
    fail fast; retry round: one hangs, one answers."""
    release = threading.Event()

    class _Fails(_Stub):
        def complete(self, *_a, **_kw):
            raise ai_client.ProviderError("boom")

    a, b = _Fails("groq", "alpha-x"), _Fails("groq", "beta-x")
    hung = _Stub("groq", "gamma-hangs", hang=release)
    fast = _Stub("groq", "delta-fast", text="retry answer")
    client = AIClient([a, b, hung, fast], cache=cache)
    monkeypatch.setattr(client, "_COUNCIL_TIMEOUT_S", 0.3)
    real = client._select_providers
    n = {"calls": 0}

    def select(k, exclude=None):
        n["calls"] += 1
        return [a, b] if n["calls"] == 1 else real(k, exclude=exclude)

    monkeypatch.setattr(client, "_select_providers", select)

    try:
        t0 = time.monotonic()
        results = client.council_generate("prompt", n_generators=2)
        elapsed = time.monotonic() - t0
    finally:
        release.set()

    assert hung.started.is_set()
    assert [r["model"] for r in results] == ["delta-fast"]
    assert elapsed < 2.0, f"retry round took {elapsed:.1f}s against a 0.3s timeout"
