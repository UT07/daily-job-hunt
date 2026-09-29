"""The council gave up with seven working providers idle.

Production, 2026-09-29 17:45 UTC. A user pressed Tailor Resume and got:

    Tailoring failed for SmokeTest Ltd — AI returned empty result

The only line behind it was:

    [Council] All generators failed — no candidates produced

No reason for any individual generator. Eleven providers, six seconds, nothing
recorded. Diagnosing it required reproducing locally against the live keys,
which showed the council was not short of capacity at all:

    OK    groq        openai/gpt-oss-120b           7.3s   9,579 chars
    OK    groq        qwen/qwen3.8-27b              7.1s  10,115 chars
    OK    groq        openai/gpt-oss-20b            4.2s   8,199 chars
    OK    nvidia      nemotron-3-super-120b-a12b   28.0s  10,980 chars
    FAIL  openrouter  minimax/minimax-m3:free              404
    FAIL  openrouter  nemotron-3-ultra-550b:free           429
    FAIL  openrouter  z-ai/glm-5.2:free                    404
    FAIL  openrouter  google/gemma-4-31b-it:free           429
    OK    qwen        qwen-plus                    55.2s  10,443 chars
    OK    qwen        qwen-turbo                   32.5s  12,816 chars
    OK    qwen        qwen-max                     49.9s  10,155 chars

7 of 11 answered a full 13,879-character tailoring prompt. Only OpenRouter's
four failed.

The bug was the retry's trigger. It read `if self._dead_providers:` — and only
401/402/403/404/410 mark a provider dead. A 429 is transient and marks nothing.
So when selection drew two OpenRouter free models and both returned 429 — their
steady state once the shared daily quota is spent — nothing was marked dead,
the retry never ran, and the council reported total failure.

On a cold Lambda `_dead_providers` starts empty, so success depended on which
two families selection happened to draw first. That is why it failed
intermittently rather than always, and why it survived every green deploy.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, ".")
import ai_client  # noqa: E402
from ai_client import AIClient  # noqa: E402


class _RateLimiter:
    """Minimal stand-in. _select_providers sorts on tokens_remaining, so a
    provider without one makes selection raise — which is a test-double
    problem, not a code problem, and it cost me a wrong diagnosis once."""

    tokens_remaining = {"minute": 100000, "day": 100000}


class _Stub:
    """A provider that either answers or raises."""

    def __init__(self, name, model, error=None, text="tailored body"):
        self.name, self.model, self._error, self._text = name, model, error, text
        self.calls = 0
        self.rate_limiter = _RateLimiter()

    # *args as well as **kwargs: the council passes the prompt positionally.
    # This double was wrong three times in a row while the code under test was
    # right — no rate_limiter, no complete_with_retry, then a keyword-only
    # signature — and each failure read as "the healthy provider was never
    # tried", i.e. exactly the production symptom. A double that fails like the
    # bug is worse than no test.
    def complete(self, *_a, **_kw):
        self.calls += 1
        if self._error:
            raise self._error
        return self._text

    def complete_with_retry(self, *a, **kw):
        return self.complete(*a, **kw)


def _rate_limited(name, model):
    return _Stub(name, model, error=ai_client.RateLimitError(f"[{name}] HTTP 429 — rate limited"))


@pytest.fixture(autouse=True)
def _deterministic_selection(monkeypatch):
    """Stop _select_providers shuffling.

    Selection randomises order to spread load. That makes "did the retry fire?"
    a coin flip in a test: with the shuffle, the first round sometimes picks the
    healthy provider directly and the retry path is never exercised at all.
    Pinning the order means the failing providers are always chosen first, so
    the healthy one can ONLY be reached through the retry — which is the
    behaviour under test.

    It is also the reason the production bug was intermittent: on a cold Lambda
    the draw decided whether a request worked.
    """
    monkeypatch.setattr(ai_client.random, "shuffle", lambda seq: None)


def _client(providers):
    # get_with_info MUST be stubbed to None. council_complete consults the
    # cache first, and a bare MagicMock returns a truthy MagicMock for any
    # method — a fake cache hit that returns before a single provider is
    # called. The symptom is "the healthy provider was never tried", which is
    # indistinguishable from the production bug this file exists to pin.
    cache = MagicMock(
        get=MagicMock(return_value=None),
        get_with_info=MagicMock(return_value=None),
        set=MagicMock(),
    )
    return AIClient(providers, cache=cache)


def _council(c, **kw):
    """Call whatever the council entry point is named, with a small prompt."""
    fn = getattr(c, "council_complete", None) or getattr(c, "complete_council", None)
    assert fn, "council entry point not found — did it get renamed?"
    return fn(prompt="tailor this", system="", n_generators=2, **kw)


def test_rate_limited_generators_do_not_end_the_council():
    """The exact production failure.

    Two 429s mark nothing dead. Before the fix the council stopped here.
    """
    dead_a = _rate_limited("openrouter", "minimax/minimax-m3:free")
    dead_b = _rate_limited("openrouter", "google/gemma-4-31b-it:free")
    healthy = _Stub("groq", "openai/gpt-oss-120b", text="a real tailored body")
    c = _client([dead_a, dead_b, healthy])

    out = _council(c)

    assert out, "council returned nothing while a healthy provider sat unused"
    assert healthy.calls >= 1, (
        "the healthy provider was never tried — the retry did not fire, which "
        "is the bug: only a DEAD provider used to trigger it, and a 429 marks "
        "nothing dead"
    )


def test_a_provider_is_not_retried_against_itself():
    """The retry must exclude what already failed, or it repeats the failure."""
    failing = _rate_limited("openrouter", "minimax/minimax-m3:free")
    healthy = _Stub("groq", "openai/gpt-oss-120b")
    c = _client([failing, healthy])
    _council(c)
    assert failing.calls <= 1, f"retried a provider that had just failed ({failing.calls}x)"


def test_no_retry_when_there_is_nothing_untried():
    """Every provider failed and none is left. Give up, do not loop."""
    a = _rate_limited("openrouter", "m1:free")
    b = _rate_limited("openrouter", "m2:free")
    c = _client([a, b])
    try:
        _council(c)
    except Exception:
        pass  # a raise is fine; an infinite loop is not
    assert a.calls <= 1 and b.calls <= 1, f"looped: {a.calls}, {b.calls}"


def test_failures_are_reported_not_swallowed():
    """'All generators failed' with no reason cost a live reproduction to
    diagnose. Every failure must name its provider and its error."""
    import inspect
    src = inspect.getsource(AIClient)
    assert "failures.append" in src, "generator failures are not recorded"
    assert "no reason recorded" in src, (
        "the all-failed log must still say something when the failure list is "
        "somehow empty, rather than printing nothing"
    )


def test_the_retry_no_longer_depends_on_a_dead_provider():
    """Guard the specific regression at the source level.

    A behavioural test can pass for the wrong reason if selection happens to
    avoid the failing providers, so pin the condition itself.
    """
    import inspect
    src = inspect.getsource(AIClient)
    assert "if self._dead_providers:\n                retry_generators" not in src, (
        "the retry is gated on _dead_providers again; a 429 marks nothing "
        "dead, so the council will give up with healthy providers available"
    )
    assert "untried = [" in src, "the retry no longer computes what is untried"
