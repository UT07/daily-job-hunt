"""LangSmith tracing must not flood the logs with a dead key.

2026-09-28: every traced call logged "Failed to POST .../runs/multipart ...
403 Forbidden" — one warning per LLM call, drowning the diagnostics the
pipeline's real failures live in, and costing a doomed round trip each time.
The existing guard only covered the SSM *fetch* failing, not the fetched key
being rejected.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "lambdas/pipeline")
from agents import graph  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    yield


def _resp(code):
    r = MagicMock()
    r.status_code = code
    return r


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_key_disables_tracing_for_the_container(code, monkeypatch):
    with patch.object(graph, "_langsmith_key_accepted", return_value=False), \
         patch("ai_helper.get_param", return_value="dead-key"):
        graph._configure_langsmith_tracing()
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert "LANGCHAIN_API_KEY" not in graph.os.environ, "a rejected key must not be exported"


def test_a_working_key_is_exported_and_tracing_stays_on():
    with patch.object(graph, "_langsmith_key_accepted", return_value=True), \
         patch("ai_helper.get_param", return_value="good-key"):
        graph._configure_langsmith_tracing()
    assert graph.os.environ["LANGCHAIN_API_KEY"] == "good-key"
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "true"


def test_an_ssm_failure_also_disables_rather_than_half_enabling():
    with patch("ai_helper.get_param", side_effect=RuntimeError("no such parameter")):
        graph._configure_langsmith_tracing()
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "false"


@pytest.mark.parametrize("code", [401, 403])
def test_probe_rejects_only_on_an_explicit_refusal(code):
    with patch("httpx.get", return_value=_resp(code)):
        assert graph._langsmith_key_accepted("k") is False


@pytest.mark.parametrize("code", [200, 404, 429, 500, 503])
def test_probe_fails_open_on_anything_else(code):
    """A network blip or a moved endpoint must not silently kill working
    tracing — only the server refusing this credential should."""
    with patch("httpx.get", return_value=_resp(code)):
        assert graph._langsmith_key_accepted("k") is True


def test_probe_fails_open_when_the_call_raises():
    with patch("httpx.get", side_effect=OSError("dns")):
        assert graph._langsmith_key_accepted("k") is True


def test_an_empty_key_is_rejected_without_a_network_call():
    with patch("httpx.get", side_effect=AssertionError("must not be called")):
        assert graph._langsmith_key_accepted("") is False


def test_tracing_off_is_a_no_op(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    with patch("ai_helper.get_param", side_effect=AssertionError("must not fetch")):
        graph._configure_langsmith_tracing()
