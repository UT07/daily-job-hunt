from unittest.mock import patch

from agents import graph as graph_mod

CAND_A = {"content": "alpha", "provider": "groq/a", "model": "openai/gpt-oss-120b"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "z-ai/glm-5.2:free"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free"}


def test_graph_compiles():
    assert graph_mod.build_council_graph() is not None


def test_end_to_end_picks_highest_scoring_candidate():
    """The critic's top score wins, whichever slot that candidate landed in.

    This used to feed call_one a fixed iterator and hand the critic positional
    scores [10, 95], which asserts an ordering the graph does not promise: the
    generate branches fan out via Send and run concurrently, so branch 1 is not
    guaranteed to consume the first item. Measured at 12 order flips in 300 runs
    on this commit and 4 in 300 on main -- a live ~1-4% CI flake, pre-existing.

    Keying the generators off the provider and reading the candidate order out
    of the critic's own prompt removes the assumption instead of the assertion.
    """
    generated = {P1["name"]: CAND_A, P2["name"]: CAND_B}

    def _call_one(provider, prompt, *args, **kwargs):
        if provider["name"] in generated:
            return generated[provider["name"]]
        # The critic. build_critique_prompt lays the candidates out in the
        # order critique_node settled on, so the prompt is the ground truth
        # for which position "beta" occupies.
        beta_first = prompt.index("beta") < prompt.index("alpha")
        return {
            "content": "[95, 10]" if beta_first else "[10, 95]",
            "provider": "c",
            "model": "m3",
        }

    with patch("agents.nodes.select_generators", return_value=[P1, P2]), \
         patch("agents.nodes.select_critic", return_value={"name": "c", "model": "meta/x"}), \
         patch("agents.nodes.call_one", side_effect=_call_one):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=2)
    assert out["content"] == "beta"


def test_return_shape_matches_legacy_contract():
    """The contract is legacy's keys, plus what only the graph can supply.

    trace_id is deliberately langgraph-only -- legacy has no tracing.
    critique_outcome and scores were added to BOTH engines on 2026-09-30, so
    they belong in the shared part: a caller that has to branch on which engine
    produced a result is how post_score silently ran guard-free for weeks.

    guard_report joined them on 2026-10-08, and this test is why it reached
    both. The graph supplies a real report; legacy has no guard nodes at all,
    so it supplies None -- the honest value for "never measured", as opposed to
    an empty report, which would claim a clean verdict from an engine that
    never looked.

    Asserted as an exact set on purpose. This test caught critique_outcome
    being added to the graph, which is what it is for -- a key appearing on one
    engine and not the other is the failure mode, and a subset check would not
    have noticed.
    """
    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND_A):
        out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=1)
    assert set(out) == {"content", "provider", "model", "trace_id",
                        "critique_outcome", "scores", "guard_report"}
    assert out["trace_id"]
    assert out["critique_outcome"] == "single_candidate", (
        "one generator means nothing to compare; the outcome must say so "
        "rather than implying a verdict"
    )


def test_both_engines_agree_on_the_shared_keys():
    """The invariant behind the test above, stated directly.

    Every key the legacy engine returns must also come back from the graph, so
    no caller needs to know which engine ran. Written as a comparison rather
    than a second literal, because two hardcoded lists drift apart silently --
    which is the same "two of everything" problem the orchestration spec is
    about.
    """
    import ai_helper

    with patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND_A):
        graph_out = graph_mod.council_complete_langgraph("p", "s", "desc", n_generators=1)

    with patch.object(ai_helper, "_select_diverse_providers", return_value=[P1]), \
         patch.object(ai_helper, "_call_provider", return_value=dict(CAND_A)), \
         patch.object(ai_helper, "_build_provider_list", return_value=[P1]):
        legacy_out = ai_helper._council_complete_legacy("p", "s", "desc", n_generators=1)

    missing = set(legacy_out) - set(graph_out)
    assert not missing, (
        f"the legacy engine returns {sorted(missing)} and the graph does not; "
        "a caller would have to branch on the engine"
    )


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


def test_the_legacy_engine_reports_no_guard_verdict_rather_than_a_clean_one():
    """`guard_report: None`, and the value matters more than the key.

    Legacy has no guard nodes at all -- ai_helper's own comment calls it
    "frozen pre-guardrail behaviour" -- so it has not measured anything.
    `None` says that. An empty report would claim a clean guard verdict from
    an engine that never looked, which is the same lie as a status that cannot
    fail, and `shared.resume_verdict` would then grade such a document `pass`
    on evidence that does not exist.

    Asserted on the PUBLIC `council_complete`, because that is where the key is
    added and where every caller enters.
    """
    import ai_helper

    with patch.object(ai_helper, "_select_diverse_providers", return_value=[P1]), \
         patch.object(ai_helper, "_call_provider", return_value=dict(CAND_A)), \
         patch.object(ai_helper, "_build_provider_list", return_value=[P1]), \
         patch.dict("os.environ", {"COUNCIL_ENGINE": "legacy"}, clear=False):
        out = ai_helper.council_complete("p", "s", "desc", n_generators=1)

    assert "guard_report" in out, "shape parity with the graph"
    assert out["guard_report"] is None, (
        "legacy has no guard nodes; an empty report would claim a clean "
        "verdict from an engine that never looked"
    )
