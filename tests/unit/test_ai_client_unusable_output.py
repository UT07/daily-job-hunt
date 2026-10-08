"""Empty or truncated provider output is a failure, not an answer.

Every `complete()` in ai_client.py returned `message.content` without looking
at it. Three shapes came back as success:

  * `content: null` — a reasoning model that spent its whole budget thinking.
    Returned as None; with skip_cache=True the caller's `.strip()` crashed,
    and with the cache on, the NOT NULL insert failed inside the try and was
    reported as a provider error against the wrong cause.
  * `content: ""` — returned as the answer.
  * `finish_reason: "length"` (Gemini: `MAX_TOKENS`) — a fragment cut off
    mid-document, returned as a finished answer AND cached for 72 hours, so
    every identical prompt replayed the fragment.

ai_helper's `_call_provider` already refuses the empty case and
`ai_complete_cached` refuses to cache truncation. This client now matches:
both are a provider failure, so failover tries the next provider and the
cache never sees them.

Doubles: the provider classes are real; only the HTTP call is replaced, and
it returns a real `requests.Response`.
"""
import json
import sys
import types

import pytest
import requests

sys.path.insert(0, ".")
import ai_client  # noqa: E402
from ai_client import (  # noqa: E402
    AIClient,
    AnthropicProvider,
    CerebrasProvider,
    GeminiProvider,
    GroqProvider,
    NvidiaNIMProvider,
    OpenRouterProvider,
    QwenProvider,
    ResponseCache,
)

OPENAI_COMPATIBLE = [GroqProvider, CerebrasProvider, OpenRouterProvider, NvidiaNIMProvider, QwenProvider]


def _response(body: dict) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps(body).encode()
    return r


def _openai_body(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def _gemini_body(text, finish_reason="STOP"):
    parts = [] if text is None else [{"text": text}]
    return {"candidates": [{"content": {"parts": parts}, "finishReason": finish_reason}]}


# (label, openai-shaped body, gemini-shaped body)
UNUSABLE = [
    ("null content", _openai_body(None), _gemini_body(None)),
    ("empty content", _openai_body(""), _gemini_body("")),
    ("whitespace only", _openai_body("  \n "), _gemini_body("  \n ")),
    ("truncated", _openai_body("half a docu", "length"), _gemini_body("half a docu", "MAX_TOKENS")),
]


class _Router:
    def __init__(self, bad_model, openai_bad, gemini_bad):
        self.bad_model, self.openai_bad, self.gemini_bad = bad_model, openai_bad, gemini_bad
        self.calls: list[str] = []

    def __call__(self, url, headers=None, json=None, timeout=None, params=None):  # noqa: A002
        if "generativelanguage" in url:
            self.calls.append("gemini")
            return _response(self.gemini_bad)
        model = json["model"]
        self.calls.append(model)
        if model == self.bad_model:
            return _response(self.openai_bad)
        return _response(_openai_body("good answer"))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ai_client.time, "sleep", lambda *_: None)


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(db_path=str(tmp_path / "cache.db"))


@pytest.mark.parametrize("cls", OPENAI_COMPATIBLE + [GeminiProvider], ids=lambda c: c.__name__)
@pytest.mark.parametrize("label,openai_bad,gemini_bad", UNUSABLE, ids=[u[0] for u in UNUSABLE])
@pytest.mark.parametrize("skip_cache", [False, True], ids=["cache-on", "cache-off"])
def test_unusable_output_fails_over_and_is_never_cached(
    monkeypatch, cache, cls, label, openai_bad, gemini_bad, skip_cache
):
    router = _Router("bad-model", openai_bad, gemini_bad)
    monkeypatch.setattr(requests, "post", router)
    client = AIClient([cls(api_key="k", model="bad-model"), GroqProvider(api_key="k", model="good-model")], cache=cache)

    info = client.complete_with_info("prompt", skip_cache=skip_cache)

    assert info["response"] == "good answer", f"{cls.__name__} {label} was returned as an answer"
    assert info["model"] == "good-model"
    assert len(router.calls) == 2, f"the bad provider must be tried exactly once: {router.calls}"
    # The cache holds the GOOD answer or nothing — never the bad one.
    cached = cache.get("prompt")
    assert cached in (None, "good answer")
    assert ("groq", "bad-model") not in client._dead_providers, "a bad answer is transient, not a dead provider"


@pytest.mark.parametrize("label,openai_bad,_g", UNUSABLE, ids=[u[0] for u in UNUSABLE])
def test_a_lone_provider_with_unusable_output_raises_instead_of_returning_it(monkeypatch, cache, label, openai_bad, _g):
    """With nothing to fail over to, the caller gets an error, not None or a
    fragment. The old code returned None here and the caller's `.strip()`
    crashed with AttributeError far from the cause."""
    monkeypatch.setattr(requests, "post", _Router("bad-model", openai_bad, None))
    client = AIClient([GroqProvider(api_key="k", model="bad-model")], cache=cache)
    with pytest.raises(ai_client.ProviderError):
        client.complete("prompt", skip_cache=True)
    assert cache.get("prompt") is None


def test_a_good_answer_still_passes_through(monkeypatch, cache):
    """Control: the check must not reject ordinary output."""
    monkeypatch.setattr(requests, "post", _Router("unused", None, None))
    client = AIClient([GroqProvider(api_key="k", model="good-model")], cache=cache)
    assert client.complete("prompt") == "good answer"
    assert cache.get("prompt") == "good answer"


def test_gemini_good_answer_passes_through(monkeypatch, cache):
    monkeypatch.setattr(requests, "post", _Router("unused", None, _gemini_body("gemini says hi")))
    client = AIClient([GeminiProvider(api_key="k", model="m")], cache=cache)
    assert client.complete("prompt") == "gemini says hi"


class _FakeAnthropic:
    def __init__(self, text, stop_reason):
        self._text, self._stop = text, stop_reason

    def module(self):
        outer = self

        class _Messages:
            def create(self, **_kw):
                block = types.SimpleNamespace(text=outer._text)
                return types.SimpleNamespace(content=[block] if outer._text is not None else [],
                                             stop_reason=outer._stop)

        class Anthropic:
            def __init__(self, api_key=None):
                self.messages = _Messages()

        return types.SimpleNamespace(Anthropic=Anthropic)


@pytest.mark.parametrize("text,stop", [(None, "end_turn"), ("", "end_turn"), ("half", "max_tokens")])
def test_anthropic_unusable_output_is_a_failure(monkeypatch, cache, text, stop):
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropic(text, stop).module())
    client = AIClient([AnthropicProvider(api_key="k", model="m")], cache=cache)
    with pytest.raises(ai_client.ProviderError):
        client.complete("prompt", skip_cache=True)


def test_anthropic_good_answer_passes_through(monkeypatch, cache):
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropic("fine", "end_turn").module())
    client = AIClient([AnthropicProvider(api_key="k", model="m")], cache=cache)
    assert client.complete("prompt") == "fine"
