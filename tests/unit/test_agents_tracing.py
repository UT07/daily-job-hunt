"""Task 10: trace_id propagation from the council graph back to callers.

council_complete_langgraph already minted a `trace_id` (uuid4) and used it as
the graph's thread_id before this task -- what was missing was including it
in the function's RETURN VALUE, so a caller (score_batch.py) can persist it
alongside a score. See graph.py's `council_complete_langgraph` docstring and
lambdas/pipeline/score_batch.py's `job_record` for the consumer side.

LangSmith itself is NOT exercised by anything in this file: whether a trace
actually reaches LangSmith depends on the LANGCHAIN_* env vars being set on
the deployed Lambda and a valid API key being live in SSM, neither of which a
unit test can observe. See
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-9-10-report.md for
exactly what remains unverified about that and how to confirm it against the
real LangSmith UI.
"""
import os
from unittest.mock import patch

from agents import graph as graph_mod

CAND = {"content": "x", "provider": "p", "model": "m"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}


def test_invoke_returns_trace_id_alongside_winner():
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND):
        out = graph_mod.council_complete_langgraph("p", "s", "d", n_generators=1)
    assert out["trace_id"]
    assert len(out["trace_id"]) == 36  # uuid4


def test_trace_id_is_unique_per_call():
    """Each call mints its own trace -- two runs must never share one, or a
    job's persisted trace_id could point at the wrong run's spans.
    """
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND):
        first = graph_mod.council_complete_langgraph("p", "s", "d", n_generators=1)
        second = graph_mod.council_complete_langgraph("p", "s", "d", n_generators=1)
    assert first["trace_id"] != second["trace_id"]


def test_trace_id_matches_the_graph_thread_id_used_for_this_run():
    """The whole point of returning trace_id is that it is a real handle onto
    this run's state: it must be the SAME id the graph was invoked with as
    `thread_id`, not an unrelated label -- otherwise a human holding the
    persisted trace_id could not find this run's checkpointer thread (today)
    or LangSmith thread (once tracing is confirmed live -- LangSmith groups
    LangGraph runs by thread_id in its Threads view, which is the mechanism
    a persisted trace_id would be looked up through).

    Bypasses the module-level cached graph entirely (patches `_get_graph`
    itself) rather than mutating `graph_mod._GRAPH`, so this test cannot
    leave a stale graph behind for any other test in the suite.
    """
    saver = graph_mod.BoundedMemorySaver(max_threads=10)
    graph = graph_mod.build_council_graph(checkpointer=saver)
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND), \
         patch.object(graph_mod, "_get_graph", return_value=graph):
        out = graph_mod.council_complete_langgraph("p", "s", "d", n_generators=1)
    assert out["trace_id"] in saver.storage


def test_configure_langsmith_tracing_noop_when_tracing_disabled(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    # _configure_langsmith_tracing does `from ai_helper import get_param`
    # INSIDE the function body (a local, not module-level, import — same
    # pattern as _db_url() above it), so the patch target is the flat
    # `ai_helper` module that import resolves against under pytest (per
    # conftest.py's sys.path setup), not an `agents.graph.get_param`
    # attribute that never exists at module scope.
    with patch("ai_helper.get_param") as mk:
        graph_mod._configure_langsmith_tracing()
    mk.assert_not_called()
    assert "LANGCHAIN_API_KEY" not in os.environ


def test_configure_langsmith_tracing_fetches_key_when_tracing_enabled(monkeypatch):
    """The plan's literal template.yaml snippet
    (`LANGCHAIN_API_KEY: "{{resolve:ssm-secure:...}}"`) fails CloudFormation
    validation for a Lambda Environment.Variables target (cfn-lint E1027 --
    verified with `sam validate --lint` while building this). This is the
    replacement: fetch the SecureString at runtime the same way every other
    secret in this codebase reaches Lambda code, once tracing is turned on
    via the (non-secret) LANGCHAIN_TRACING_V2 env var template.yaml does set.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    with patch("ai_helper.get_param", return_value="sk-test-key") as mk:
        graph_mod._configure_langsmith_tracing()
        # Assert inside the patch context, before LANGCHAIN_API_KEY is
        # cleaned up below and before monkeypatch unwinds LANGCHAIN_TRACING_V2.
        mk.assert_called_once_with("/naukribaba/LANGCHAIN_API_KEY")
        assert os.environ["LANGCHAIN_API_KEY"] == "sk-test-key"
    del os.environ["LANGCHAIN_API_KEY"]  # don't leak into other tests


def test_configure_langsmith_tracing_does_not_overwrite_existing_key(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "already-set")
    with patch("ai_helper.get_param") as mk:
        graph_mod._configure_langsmith_tracing()
    mk.assert_not_called()


def test_configure_langsmith_tracing_swallows_ssm_failure(monkeypatch):
    """Load-bearing: a broken/unreachable SSM call for the tracing key must
    never propagate and take generation down with it.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    with patch("ai_helper.get_param", side_effect=RuntimeError("boom")):
        graph_mod._configure_langsmith_tracing()  # must not raise
    assert "LANGCHAIN_API_KEY" not in os.environ


def test_legacy_engine_result_has_no_trace_id(monkeypatch):
    """Documents the deliberate asymmetry: legacy has no tracing at all, so
    `ai_helper.council_complete(...)` under COUNCIL_ENGINE=legacy must not
    grow a trace_id key -- score_batch.py's `score_result.get("trace_id")`
    relies on this being cleanly absent (-> None instead of a fabricated
    value), not present-but-fake.
    """
    monkeypatch.delenv("COUNCIL_ENGINE", raising=False)
    from lambdas.pipeline import ai_helper

    with patch.object(ai_helper, "_build_provider_list", return_value=[P1]), \
         patch.object(ai_helper, "_call_provider", return_value=CAND), \
         patch.object(ai_helper, "_select_diverse_providers", return_value=[P1]):
        result = ai_helper.council_complete("p", "s", "d", n_generators=1)
    assert "trace_id" not in result
