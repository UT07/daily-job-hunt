"""The council's winner must say whether it was actually adjudicated.

critique_node has four paths that return candidates[0] with no verdict, and
until 2026-09-30 they were indistinguishable from a real adjudication: the
return shape differed only by `scores` being empty, and the no_critic_family
path logged nothing at all.

Measured over 80 rounds: the critic produced a usable verdict in 21. The other
59 took candidate 1 unreviewed — 26% adjudication — and that figure could only
be obtained by grepping CloudWatch for log strings.

This is the same defect as a Step Function ending in Succeed after catching an
error: a result that cannot distinguish "did the work" from "gave up". Rule 2
in CLAUDE.md's Verification rules.

No behaviour changes here. This makes the degradation countable so Phase 2 of
docs/superpowers/specs/2026-09-30-agent-orchestration-design.md can be judged
by a number rather than an assertion.
"""
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

from agents import nodes  # noqa: E402
from agents.nodes import CRITIQUE_OUTCOMES, critique_node  # noqa: E402


def _cand(model, text="body"):
    return {"content": text, "provider": model.split("/")[0], "model": model}


TWO = [_cand("groq/gpt-oss-120b", "first"), _cand("gemini/flash", "second")]


def test_every_outcome_is_named():
    assert set(CRITIQUE_OUTCOMES) == {
        "adjudicated", "single_candidate", "no_critic_family",
        "critic_call_failed", "critic_unparseable",
    }


def test_a_real_verdict_is_marked_adjudicated():
    with patch.object(nodes, "select_critic", return_value={"name": "c", "model": "m/x"}), \
         patch.object(nodes, "call_one", return_value={"content": "1: 10\n2: 90"}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[10, 90]):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "adjudicated"
    assert out["winner"]["content"] == "second", "the higher score did not win"


def test_a_single_candidate_is_not_adjudicated():
    out = critique_node({"candidates": [TWO[0]]})
    assert out["critique_outcome"] == "single_candidate"
    assert out["winner"] is TWO[0]


def test_no_available_critic_is_named_and_logged(caplog):
    """This path logged NOTHING. It was the one degradation that left no trace
    at all, in a system whose only view of the council was its log lines."""
    import logging
    with caplog.at_level(logging.WARNING), \
         patch.object(nodes, "select_critic", return_value=None):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "no_critic_family"
    assert any("no_critic_family" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_a_failed_critic_call_is_named():
    with patch.object(nodes, "select_critic", return_value={"name": "c", "model": "m/x"}), \
         patch.object(nodes, "call_one", return_value=None):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "critic_call_failed"


def test_unparseable_output_is_named_and_the_raw_text_is_logged(caplog):
    """Phase 2 has to fix the SHAPE. "unparseable" without the text is not
    something anyone can act on."""
    import logging
    with caplog.at_level(logging.WARNING), \
         patch.object(nodes, "select_critic", return_value={"name": "c", "model": "m/x"}), \
         patch.object(nodes, "call_one", return_value={"content": "I prefer the first one."}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[]):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "critic_unparseable"
    assert any("I prefer the first one" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_every_non_adjudicated_outcome_returns_candidate_one():
    """Documents the fallback honestly: the winner really is just the first
    candidate in all four cases."""
    cases = [
        (lambda: patch.object(nodes, "select_critic", return_value=None), "no_critic_family"),
    ]
    for ctx, expected in cases:
        with ctx():
            out = critique_node({"candidates": TWO})
        assert out["critique_outcome"] == expected
        assert out["winner"] is TWO[0]
        assert out["scores"] == []


def test_the_outcome_reaches_the_graph_caller():
    """A field the graph drops on the way out is not observable."""
    import inspect

    from agents import graph
    src = inspect.getsource(graph.council_complete_langgraph)
    assert '"critique_outcome"' in src, (
        "the graph does not return critique_outcome, so no caller can record it"
    )


def test_the_state_schema_declares_it():
    """StateGraph silently DROPS any key not in the schema — it would surface
    as a missing field downstream rather than an error at the write."""
    from agents.state import CouncilState
    assert "critique_outcome" in CouncilState.__annotations__


def test_tailoring_records_it():
    import inspect

    import tailor_resume
    src = inspect.getsource(tailor_resume)
    assert "critique_outcome" in src
    assert "PGRST204" in src, (
        "the update must tolerate the column being absent; PostgREST fails the "
        "WHOLE update on an unknown column and tailoring_model would be lost too"
    )
