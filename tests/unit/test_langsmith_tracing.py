"""LangSmith tracing must not flood the logs with a dead key.

2026-09-28: every traced call logged "Failed to POST .../runs/multipart ...
403 Forbidden" — one warning per LLM call, drowning the diagnostics the
pipeline's real failures live in, and costing a doomed round trip each time.
The existing guard only covered the SSM *fetch* failing, not the fetched key
being rejected.

This file owns the ENVIRONMENT invariants: which of LANGCHAIN_TRACING_V2 and
LANGCHAIN_API_KEY end up set, per outcome. The status tokens, the log level and
wording of each outcome, and the probe's `detail` are owned by
test_langsmith_observability.py.
"""
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, "lambdas/pipeline")
from agents import graph  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.setattr(graph, "_LANGSMITH_STATUS", None, raising=False)
    yield


def _refused(detail="HTTP 403 from https://api.smith.langchain.com/api/v1/sessions?limit=1"):
    return graph.LangSmithProbe(False, detail)


def test_a_rejected_key_disables_tracing_for_the_container():
    with patch.object(graph, "_langsmith_probe", return_value=_refused()), \
         patch("ai_helper.get_param", return_value="dead-key"):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF_KEY_REJECTED
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert "LANGCHAIN_API_KEY" not in graph.os.environ, "a rejected key must not be exported"


def test_a_working_key_is_exported_and_tracing_stays_on():
    with patch.object(graph, "_langsmith_probe",
                      return_value=graph.LangSmithProbe(True, "HTTP 200")), \
         patch("ai_helper.get_param", return_value="good-key"):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_ON
    assert graph.os.environ["LANGCHAIN_API_KEY"] == "good-key"
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "true"


def test_an_ssm_failure_also_disables_rather_than_half_enabling():
    with patch("ai_helper.get_param", side_effect=RuntimeError("no such parameter")):
        graph._configure_langsmith_tracing()
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_an_empty_parameter_also_disables_rather_than_half_enabling():
    """Same end state as a rejection — tracing off, no key exported — reached
    by a different cause, so the exporter never starts and never retries."""
    with patch("ai_helper.get_param", return_value=""):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF_KEY_ABSENT
    assert graph.os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert "LANGCHAIN_API_KEY" not in graph.os.environ


def test_an_empty_key_is_refused_without_a_network_call():
    with patch("httpx.get", side_effect=AssertionError("must not be called")):
        assert graph._langsmith_probe("").accepted is False


def test_tracing_off_is_a_no_op(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    with patch("ai_helper.get_param", side_effect=AssertionError("must not fetch")):
        assert graph._configure_langsmith_tracing() == graph.LANGSMITH_OFF
