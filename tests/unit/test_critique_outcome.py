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


# Three critics from distinct families, which is what select_critics returns.
C1 = {"name": "nvidia/nemotron-3-super-120b-a12b", "model": "nvidia/nemotron"}
C2 = {"name": "groq/gpt-oss-20b", "model": "groq/gpt-oss"}
C3 = {"name": "gemini/gemini-3.6-flash", "model": "gemini/flash"}


def test_every_outcome_is_named():
    assert set(CRITIQUE_OUTCOMES) == {
        "adjudicated", "single_candidate", "no_critic_family",
        "critic_call_failed", "critic_unparseable",
    }


def test_a_real_verdict_is_marked_adjudicated():
    with patch.object(nodes, "select_critics", return_value=[C1]), \
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
         patch.object(nodes, "select_critics", return_value=[]):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "no_critic_family"
    assert any("no_critic_family" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_a_failed_critic_call_is_named():
    with patch.object(nodes, "select_critics", return_value=[C1]), \
         patch.object(nodes, "call_one", return_value=None):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "critic_call_failed"


def test_unparseable_output_is_named_and_the_raw_text_is_logged(caplog):
    """Phase 2 has to fix the SHAPE. "unparseable" without the text is not
    something anyone can act on."""
    import logging
    with caplog.at_level(logging.WARNING), \
         patch.object(nodes, "select_critics", return_value=[C1]), \
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
        (lambda: patch.object(nodes, "select_critics", return_value=[]), "no_critic_family"),
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


# ---------------------------------------------------------------------------
# One critic was a single point of failure
#
# Measured in production 2026-09-30 across one real batch: every adjudication
# that did not happen came from this slot, never from generation.
#
#   [council] outcome=critic_call_failed — critic nvidia/nemotron-3-super-120b-a12b
#             returned nothing; returning candidate 1 unadjudicated
#   [council] outcome=critic_call_failed — critic nvidia/nemotron-3-ultra-550b-a55b
#             returned nothing
#   [council] outcome=critic_unparseable — ... Raw: 'We need to evaluate two
#             candidates based on criteria: ACCURACY, COMPLETENESS...'
#
# The last one is the model narrating its plan instead of emitting the JSON.
# The council gave up on the first failure every time and shipped candidate 1
# unreviewed, which is the whole point of having a council.
# ---------------------------------------------------------------------------

def test_a_second_critic_is_tried_when_the_first_returns_nothing():
    calls = []

    def _call(critic, *a, **kw):
        calls.append(critic["name"])
        return None if critic is C1 else {"content": "1: 10\n2: 90"}

    with patch.object(nodes, "select_critics", return_value=[C1, C2, C3]), \
         patch.object(nodes, "call_one", side_effect=_call), \
         patch.object(nodes, "_parse_critic_scores", return_value=[10, 90]):
        out = critique_node({"candidates": TWO})

    assert out["critique_outcome"] == "adjudicated"
    assert out["winner"]["content"] == "second"
    assert calls == [C1["name"], C2["name"]], "did not stop at the first success"


def test_an_unparseable_first_critic_does_not_stop_the_second():
    """The production failure: NVIDIA answers with prose, and that was fatal."""
    def _scores(content, n):
        return [] if content.startswith("We need to evaluate") else [90, 10]

    with patch.object(nodes, "select_critics", return_value=[C1, C2]), \
         patch.object(nodes, "call_one", side_effect=[
             {"content": "We need to evaluate two candidates based on criteria"},
             {"content": "1: 90\n2: 10"}]), \
         patch.object(nodes, "_parse_critic_scores", side_effect=_scores):
        out = critique_node({"candidates": TWO})

    assert out["critique_outcome"] == "adjudicated"
    assert out["winner"]["content"] == "first"


def test_all_critics_failing_says_how_many_were_tried(caplog):
    """An outcome that cannot distinguish "one critic was unlucky" from "every
    family refused" is the shape rule 2 warns about. The vocabulary is
    unchanged -- it is recorded in jobs.critique_outcome and old rows must stay
    comparable -- so the count lives in the log."""
    import logging
    with caplog.at_level(logging.WARNING), \
         patch.object(nodes, "select_critics", return_value=[C1, C2, C3]), \
         patch.object(nodes, "call_one", return_value=None):
        out = critique_node({"candidates": TWO})

    assert out["critique_outcome"] == "critic_call_failed"
    assert out["winner"] is TWO[0]
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "all 3 critic(s) failed" in text, text
    for critic in (C1, C2, C3):
        assert critic["name"] in text, f"{critic['name']} missing from {text}"


def test_unparseable_wins_the_outcome_when_any_critic_was_unreadable():
    """Two ways to fail; the outcome must name the one that actually happened.
    Reporting "returned nothing" for a critic that answered would send the next
    reader after the wrong provider."""
    with patch.object(nodes, "select_critics", return_value=[C1, C2]), \
         patch.object(nodes, "call_one", side_effect=[
             {"content": "prose, not JSON"}, None]), \
         patch.object(nodes, "_parse_critic_scores", return_value=[]):
        out = critique_node({"candidates": TWO})
    assert out["critique_outcome"] == "critic_unparseable"


def test_critics_are_requested_from_families_that_did_not_generate():
    """Families, not models: a second NVIDIA entry fails the same way."""
    seen = {}

    def _select(exclude_families, n=1):
        seen["exclude"] = set(exclude_families)
        seen["n"] = n
        return [C2]

    with patch.object(nodes, "select_critics", side_effect=_select), \
         patch.object(nodes, "call_one", return_value={"content": "x"}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[1, 2]):
        critique_node({"candidates": TWO})

    assert seen["n"] == nodes.CRITIC_ATTEMPTS > 1, "only one critic was requested"
    assert seen["exclude"], "the generators' families were not excluded"


# ---------------------------------------------------------------------------
# Making adjudication work exposed what adjudication selects on
#
# With one critic that almost always failed, the winner was candidate 1 and the
# guards saw whatever that happened to be. Retrying across three critics made
# real verdicts common, and the AI Eval Gate measured the consequence twice,
# reproducibly: guard_pass_rate 0.92 -> 0.88 on PR #182. The critic was doing
# its job -- picking the best-written candidate -- and best-written is not the
# same as structurally sound. Same argument prefer_complete already makes about
# truncation, one level up.
# ---------------------------------------------------------------------------

def _two(a_content, b_content):
    return [{"content": a_content, "model": "m/a", "provider": "p"},
            {"content": b_content, "model": "m/b", "provider": "p"}]


def test_a_guard_failing_candidate_is_not_offered_to_the_critic():
    passed = {"BAD": False, "GOOD": True}
    seen = {}

    def _check(content, *a, **kw):
        return type("R", (), {"passed": passed[content]})()

    def _critique(cands, *a, **kw):
        seen["n"] = len(cands)
        return "prompt"

    with patch.object(nodes, "check_output", side_effect=_check), \
         patch.object(nodes, "build_critique_prompt", side_effect=_critique), \
         patch.object(nodes, "select_critics", return_value=[C1]), \
         patch.object(nodes, "call_one", return_value={"content": "x"}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[50]):
        out = critique_node({"candidates": _two("BAD", "GOOD")})

    # Only one survived the filter, so there is nothing to adjudicate.
    assert out["critique_outcome"] == "single_candidate"
    assert out["winner"]["content"] == "GOOD", "the guard-failing candidate won"
    assert "n" not in seen, "the critic was asked to judge anyway"


def test_the_filter_fails_open_when_every_candidate_is_blocked():
    """A blocked answer is still an answer. The caller's gate decides, exactly
    as prefer_complete leaves a fully-truncated set alone."""
    with patch.object(nodes, "check_output",
                      side_effect=lambda *a, **kw: type("R", (), {"passed": False})()), \
         patch.object(nodes, "select_critics", return_value=[C1]), \
         patch.object(nodes, "call_one", return_value={"content": "x"}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[10, 90]):
        out = critique_node({"candidates": _two("BAD1", "BAD2")})
    assert out["critique_outcome"] == "adjudicated"
    assert out["winner"]["content"] == "BAD2", "the critic never got both candidates"


def test_a_clean_set_is_passed_through_untouched():
    with patch.object(nodes, "check_output",
                      side_effect=lambda *a, **kw: type("R", (), {"passed": True})()), \
         patch.object(nodes, "select_critics", return_value=[C1]), \
         patch.object(nodes, "call_one", return_value={"content": "x"}), \
         patch.object(nodes, "_parse_critic_scores", return_value=[90, 10]):
        out = critique_node({"candidates": _two("GOOD1", "GOOD2")})
    assert out["critique_outcome"] == "adjudicated"
    assert out["winner"]["content"] == "GOOD1"


def test_a_single_candidate_is_never_guard_checked():
    """Nothing to choose between, and check_output is not free."""
    calls = []
    with patch.object(nodes, "check_output",
                      side_effect=lambda *a, **kw: calls.append(1) or type("R", (), {"passed": False})()):
        out = critique_node({"candidates": [TWO[0]]})
    assert out["critique_outcome"] == "single_candidate"
    assert calls == [], "check_output ran on a one-candidate set"


def test_the_discard_is_logged(caplog):
    import logging
    passed = {"BAD": False, "GOOD": True}
    with caplog.at_level(logging.WARNING), \
         patch.object(nodes, "check_output",
                      side_effect=lambda c, *a, **kw: type("R", (), {"passed": passed[c]})()):
        critique_node({"candidates": _two("BAD", "GOOD")})
    assert any("guard-failing candidate" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
