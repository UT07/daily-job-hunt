from unittest.mock import patch

from agents import graph as graph_mod

CAND_A = {"content": "alpha", "provider": "groq/a", "model": "openai/gpt-oss-120b"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "z-ai/glm-5.2:free"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free"}


def test_graph_compiles():
    assert graph_mod.build_council_graph() is not None


def test_end_to_end_picks_highest_scoring_candidate():
    calls = iter([CAND_A, CAND_B, {"content": "[10, 95]", "provider": "c", "model": "m3"}])
    with patch("agents.nodes.select_generators", return_value=[P1, P2]), \
         patch("agents.nodes.select_critic", return_value={"name": "c", "model": "meta/x"}), \
         patch("agents.nodes.call_one", side_effect=lambda *a, **k: next(calls)):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)
    assert out["content"] == "beta"


def test_return_shape_matches_legacy_contract():
    """council_complete_langgraph's contract is legacy's three keys PLUS
    trace_id (Task 10) -- legacy has no tracing, so trace_id is intentionally
    a langgraph-only addition, not a fourth key legacy is expected to grow.
    """
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND_A):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=1)
    assert set(out) == {"content", "provider", "model", "trace_id"}
    assert out["trace_id"]


def test_raises_when_every_generator_fails():
    import pytest
    with patch("agents.nodes.select_generators", return_value=[P1, P2]), \
         patch("agents.nodes.call_one", return_value=None):
        with pytest.raises(RuntimeError, match="all generators failed"):
            graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)


def test_repair_round_resets_candidates_not_accumulates():
    """A permanently-failing guard_output_node evaluation forces two repair
    rounds (quality_gate's repair_attempts>=2 cap means three total generate
    passes before the graph finalizes). Each round must start from an empty
    candidate list -- otherwise rejected candidates from earlier rounds pile
    back up, get re-scored alongside the new batch, and can win despite
    being rejected.

    Drives failure via real content (task="tailor", missing every required
    LaTeX section -- a block-severity violation) rather than a hand-seeded
    `guard_report`. Now that guard_output_node is wired into the graph (Task
    22), it recomputes guard_report from the ACTUAL winner content after
    every critique round; a hand-seeded value would be silently overwritten
    by a real (passing, since CAND_A's plain "alpha" content trips no
    checks) evaluation on round one, and this test would report only one
    generate pass -- happening to still satisfy the assertion below with
    len==1, but for the wrong reason (0 repairs, not 2). See
    test_agents_guard_nodes.py::test_guard_output_wiring_arms_the_bounded_repair_loop
    for the dedicated wiring regression this mirrors.
    """
    bad_cand = {"content": "No LaTeX sections at all.", "provider": "groq/a", "model": "m1"}
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=bad_cand):
        graph = graph_mod.build_council_graph()
        final = graph.invoke(
            {
                "task": "tailor",
                "prompt": "p",
                "system": "s",
                "task_description": "desc",
                "n_generators": 1,
                "temperature": 0.3,
                "candidates": [],
                "repair_attempts": 0,
                "trace_id": "test-repair-reset",
            },
            config={"configurable": {"thread_id": "test-repair-reset"}},
        )
    assert final["repair_attempts"] == 2
    # Three generate passes run (initial attempt + 2 repairs) before the
    # repair budget is exhausted, but candidates must reflect only the final
    # round's output -- one generator's worth, not three rounds concatenated.
    assert len(final["candidates"]) == 1
