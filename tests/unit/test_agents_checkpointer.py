"""Task 9: bounded in-memory checkpointer, with the Postgres seam mocked out.

There is no `/naukribaba/SUPABASE_DB_URL` SSM parameter in this project today
(verified against the live parameter store while building this feature —
only SUPABASE_URL and SUPABASE_SERVICE_KEY exist for the PostgREST client).
`test_checkpointer_uses_postgres_when_db_url_present` therefore never
exercises a real Postgres connection anywhere in this suite: it patches
`_postgres_saver` itself, so `agents.graph`'s only import of
`langgraph.checkpoint.postgres` (inside `_postgres_saver`'s body) is never
actually reached. That import staying unreachable is deliberate — see
graph.py's module-level comment and .superpowers/sdd/2026-09-22-ey-genai-
platform-upgrade/task-9-10-report.md for why `langgraph-checkpoint-postgres`
was not added to requirements.txt.
"""
from unittest.mock import patch

from agents import graph as graph_mod

CAND = {"content": "x", "provider": "p", "model": "m"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}


def test_checkpointer_is_memory_when_db_url_absent():
    from langgraph.checkpoint.memory import MemorySaver
    with patch.object(graph_mod, "_db_url", return_value=None):
        assert isinstance(graph_mod.get_checkpointer(), MemorySaver)


def test_checkpointer_is_specifically_the_bounded_subclass_when_db_url_absent():
    """Stronger than the isinstance-against-MemorySaver check above: a plain
    unbounded `MemorySaver()` would also satisfy that assertion, silently
    reopening the leak this task closes. Pin down the concrete type too.
    """
    with patch.object(graph_mod, "_db_url", return_value=None):
        cp = graph_mod.get_checkpointer()
    assert isinstance(cp, graph_mod.BoundedMemorySaver)


def test_checkpointer_uses_postgres_when_db_url_present():
    sentinel = object()
    with patch.object(graph_mod, "_db_url", return_value="postgresql://x"), \
         patch.object(graph_mod, "_postgres_saver", return_value=sentinel) as mk:
        assert graph_mod.get_checkpointer() is sentinel
    mk.assert_called_once_with("postgresql://x")


def test_get_checkpointer_falls_back_to_bounded_saver_when_postgres_saver_raises():
    """The load-bearing case: a reachable-but-broken DB URL (bad connection
    string, network partition, missing dependency) must fall back exactly
    like the no-URL case, not raise and take generation down with it, and
    not fall back to a bare unbounded MemorySaver either -- a DB that is
    flaky rather than cleanly absent shouldn't be able to reopen the leak.
    """
    with patch.object(graph_mod, "_db_url", return_value="postgresql://x"), \
         patch.object(graph_mod, "_postgres_saver", side_effect=RuntimeError("boom")):
        cp = graph_mod.get_checkpointer()
    assert isinstance(cp, graph_mod.BoundedMemorySaver)


def test_max_checkpoint_threads_defaults_to_200(monkeypatch):
    monkeypatch.delenv("COUNCIL_CHECKPOINT_MAX_THREADS", raising=False)
    assert graph_mod._max_checkpoint_threads() == 200


def test_max_checkpoint_threads_honours_env_override(monkeypatch):
    monkeypatch.setenv("COUNCIL_CHECKPOINT_MAX_THREADS", "5")
    assert graph_mod._max_checkpoint_threads() == 5


def test_max_checkpoint_threads_falls_back_on_garbage_env_value(monkeypatch):
    monkeypatch.setenv("COUNCIL_CHECKPOINT_MAX_THREADS", "not-a-number")
    assert graph_mod._max_checkpoint_threads() == 200


def test_bounded_memory_saver_evicts_oldest_thread_when_over_capacity():
    """Direct, minimal exercise of the eviction mechanism itself: three
    distinct threads through a saver capped at two must leave exactly the
    two most-recently-written threads behind, using the same public
    `delete_thread` primitive InMemorySaver ships (not a hand-rolled reach
    into `storage`/`writes`/`blobs`).
    """
    saver = graph_mod.BoundedMemorySaver(max_threads=2)
    for i in range(3):
        config = {"configurable": {"thread_id": f"t{i}", "checkpoint_ns": ""}}
        checkpoint = {"id": f"cp{i}", "channel_values": {}}
        saver.put(config, checkpoint, {}, {})

    assert set(saver.storage.keys()) == {"t1", "t2"}
    assert "t0" not in saver.storage


def test_bounded_memory_saver_one_run_many_checkpoints_counts_as_one_thread():
    """A single council run writes several checkpoints (one per node
    transition) to the SAME thread_id. That must count once against the cap,
    not once per checkpoint -- otherwise a single run could evict itself.
    """
    saver = graph_mod.BoundedMemorySaver(max_threads=1)
    config = {"configurable": {"thread_id": "same-thread", "checkpoint_ns": ""}}
    for i in range(5):
        checkpoint = {"id": f"cp{i}", "channel_values": {}}
        saver.put(config, checkpoint, {}, {})

    assert set(saver.storage.keys()) == {"same-thread"}
    # All 5 checkpoints for this one thread/ns are retained -- bounding is by
    # distinct thread count, not by checkpoint count within a thread.
    assert len(saver.storage["same-thread"][""]) == 5


def test_bounded_memory_saver_evicts_across_real_graph_invocations():
    """End-to-end version of the same guarantee: drive the REAL compiled
    graph (not hand-built checkpoint dicts) through more council runs than
    the configured cap, exactly as council_complete_langgraph does in
    production (one fresh uuid4 thread_id per call), and confirm old threads
    are actually forgotten rather than merely capped in theory.
    """
    saver = graph_mod.BoundedMemorySaver(max_threads=3)
    graph = graph_mod.build_council_graph(checkpointer=saver)
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND):
        for i in range(5):
            tid = f"thread-{i}"
            graph.invoke(
                {
                    "task": "default",
                    "prompt": "p",
                    "system": "s",
                    "task_description": "d",
                    "n_generators": 1,
                    "temperature": 0.3,
                    "candidates": [],
                    "repair_attempts": 0,
                    "trace_id": tid,
                },
                config={"configurable": {"thread_id": tid}},
            )

    assert len(saver.storage) == 3
    assert set(saver.storage) == {"thread-2", "thread-3", "thread-4"}


def test_get_graph_wires_checkpointer_through_build_council_graph():
    """_get_graph() must actually call get_checkpointer() and hand the result
    to build_council_graph -- not silently keep compiling with the bare
    MemorySaver() default, which would make Task 9 a no-op for the one path
    (council_complete_langgraph, via the module-level cached graph) that
    matters in production.

    Resets the module-level cache before AND after so this test cannot leak
    a stale graph into, or pick one up from, any other test in the suite
    (agents/graph.py caches _GRAPH at module scope — see Task 9/10 cautions).
    """
    original_graph = graph_mod._GRAPH
    sentinel_checkpointer = object()
    try:
        graph_mod._GRAPH = None
        # build_council_graph is fully replaced (not `wraps=`d through to the
        # real implementation): the real StateGraph.compile() validates its
        # checkpointer argument and would reject this test's plain-object
        # sentinel, but the point of this test is only to prove _get_graph
        # WIRES get_checkpointer()'s result into build_council_graph, not to
        # exercise a real compile with a fake checkpointer.
        with patch.object(graph_mod, "get_checkpointer", return_value=sentinel_checkpointer) as mk, \
             patch.object(graph_mod, "build_council_graph") as build_mk:
            graph_mod._get_graph()
        mk.assert_called_once()
        build_mk.assert_called_once_with(checkpointer=sentinel_checkpointer)
    finally:
        graph_mod._GRAPH = original_graph
