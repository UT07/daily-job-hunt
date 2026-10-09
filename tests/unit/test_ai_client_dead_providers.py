"""A dead provider must actually be marked dead.

`AIClient._DEAD_CODES` lists 401/402/403/404/410, and a test asserted 410 was
in the set. Nothing asserted that the set was ever CONSULTED with a real
status, and it was not. Every call site read:

    status = e.response.status_code if hasattr(e, 'response') and e.response else 0

`requests.Response.__bool__` is `self.ok`, i.e. False for every 4xx and 5xx.
So for exactly the responses the set exists to catch, `e.response` was falsy,
`status` became 0, `0 in _DEAD_CODES` was False, and a 410-Gone model was
retried on every request for the life of the process. A detector that is wired
to nothing is CLAUDE.md #13: these tests assert what happens when it fires.

The doubles here are deliberately thin. The provider classes are the REAL
ones; only `requests.post` is replaced, and it returns a real
`requests.Response`, so `raise_for_status()` raises a real `HTTPError` carrying
a real (falsy) response — the exact object the bug mishandled. A MagicMock
response is truthy and would have hidden the bug (CLAUDE.md #6).
"""
import json
import sys

import pytest
import requests

sys.path.insert(0, ".")
import ai_client  # noqa: E402
from ai_client import AIClient, GroqProvider, ResponseCache  # noqa: E402


def _response(status: int, body: dict) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.url = "https://api.groq.com/openai/v1/chat/completions"
    r.reason = {200: "OK", 410: "Gone", 404: "Not Found", 429: "Too Many Requests"}.get(status, "")
    return r


def _ok(text: str) -> requests.Response:
    return _response(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


class _Router:
    """requests.post replacement: answers per model id, counts calls per model."""

    def __init__(self, statuses: dict[str, int]):
        self.statuses = statuses
        self.calls: dict[str, int] = {}

    def __call__(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        model = json["model"]
        self.calls[model] = self.calls.get(model, 0) + 1
        status = self.statuses[model]
        if status == 200:
            return _ok(f"answer from {model}")
        return _response(status, {"error": {"message": f"{model} is gone"}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ai_client.time, "sleep", lambda *_: None)


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(db_path=str(tmp_path / "cache.db"))


def _client(cache, *models):
    return AIClient([GroqProvider(api_key="k", model=m) for m in models], cache=cache)


def test_the_double_produces_the_falsy_response_the_bug_mishandled():
    """Prove the instrument first: if this Response were truthy, every test
    below would pass against the unfixed code and prove nothing."""
    err = None
    try:
        _response(410, {}).raise_for_status()
    except requests.HTTPError as e:
        err = e
    assert err is not None and err.response is not None
    assert err.response.status_code == 410
    assert not bool(err.response), "a 4xx Response is falsy; that is the whole bug"


@pytest.mark.parametrize("status", sorted(AIClient._DEAD_CODES))
def test_complete_marks_a_dead_provider_and_never_calls_it_again(monkeypatch, cache, status):
    router = _Router({"alpha-dead": status, "beta-live": 200})
    monkeypatch.setattr(requests, "post", router)
    client = _client(cache, "alpha-dead", "beta-live")

    assert client.complete("p1", skip_cache=True) == "answer from beta-live"
    assert ("groq", "alpha-dead") in client._dead_providers

    client.complete("p2", skip_cache=True)
    assert router.calls["alpha-dead"] == 1, (
        f"a provider that returned {status} was called again: {router.calls}"
    )


def test_complete_with_info_marks_a_dead_provider(monkeypatch, cache):
    router = _Router({"alpha-dead": 410, "beta-live": 200})
    monkeypatch.setattr(requests, "post", router)
    client = _client(cache, "alpha-dead", "beta-live")

    assert client.complete_with_info("p1", skip_cache=True)["model"] == "beta-live"
    client.complete_with_info("p2", skip_cache=True)
    assert router.calls["alpha-dead"] == 1, router.calls


def test_a_transient_5xx_does_not_mark_a_provider_dead(monkeypatch, cache):
    """Control: the fix must not make every HTTP error permanent."""
    router = _Router({"alpha-flaky": 503, "beta-live": 200})
    monkeypatch.setattr(requests, "post", router)
    client = _client(cache, "alpha-flaky", "beta-live")

    client.complete("p1", skip_cache=True)
    assert client._dead_providers == set()


def test_council_generate_marks_a_dead_generator(monkeypatch, cache):
    router = _Router({"alpha-dead": 410, "beta-live": 200})
    monkeypatch.setattr(requests, "post", router)
    client = _client(cache, "alpha-dead", "beta-live")

    results = client.council_generate("p", n_generators=2)
    assert [r["model"] for r in results] == ["beta-live"]
    assert ("groq", "alpha-dead") in client._dead_providers


def test_council_retry_round_marks_a_dead_generator(monkeypatch, cache):
    """The retry round had its own copy of the idiom. Force the first round to
    fail transiently (503 marks nothing) so the retry draws the 410 model."""
    router = _Router({"alpha-flaky": 503, "beta-flaky": 503, "gamma-dead": 410, "delta-live": 200})
    monkeypatch.setattr(requests, "post", router)
    client = _client(cache, "alpha-flaky", "beta-flaky", "gamma-dead", "delta-live")
    # Pin the first round to the two flaky providers so the retry round is
    # what meets the dead one, whatever order selection shuffles into.
    real_select = client._select_providers
    first = [p for p in client.providers if p.model in ("alpha-flaky", "beta-flaky")]
    calls = {"n": 0}

    def select(n, exclude=None):
        calls["n"] += 1
        return first if calls["n"] == 1 else real_select(n, exclude=exclude)

    monkeypatch.setattr(client, "_select_providers", select)

    results = client.council_generate("p", n_generators=2)
    assert [r["model"] for r in results] == ["delta-live"]
    assert router.calls.get("gamma-dead") == 1, router.calls
    assert ("groq", "gamma-dead") in client._dead_providers
