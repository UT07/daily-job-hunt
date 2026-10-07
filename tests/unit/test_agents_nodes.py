from unittest.mock import patch

from langgraph.types import Overwrite

from agents import nodes

from tests.unit.realistic_resume_body import body as realistic_body

PROV = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
# Distinct model family from PROV ("gpt-oss" vs "glm") — see
# lambdas.pipeline.ai_helper._model_family.
FALLBACK_PROV = {"name": "or/b", "model": "z-ai/glm-5.2:free"}
CAND_A = {"content": "alpha", "provider": "groq/a", "model": "m1"}
CAND_B = {"content": "beta", "provider": "or/b", "model": "m2"}


def test_plan_node_records_chosen_generators():
    with patch.object(nodes, "select_generators", return_value=[PROV]):
        out = nodes.plan_node({"n_generators": 1, "prompt": "p"})
    assert out["generators"] == [PROV]


def test_generate_node_wraps_result_in_list_for_reducer():
    with patch.object(nodes, "call_one", return_value=CAND_A):
        out = nodes.generate_node({"provider": PROV, "prompt": "p", "system": "", "temperature": 0.3})
    assert out == {"candidates": [CAND_A]}


def test_generate_node_contributes_empty_list_on_failure():
    # A dead provider must not poison the reducer with None.
    with patch.object(nodes, "call_one", return_value=None):
        out = nodes.generate_node({"provider": PROV, "prompt": "p", "system": "", "temperature": 0.3})
    assert out == {"candidates": []}


def test_generate_node_success_path_does_not_touch_fallback():
    # The common case: primary succeeds, fallback pool must never be consulted.
    with patch.object(nodes, "all_providers") as mock_all_providers, \
         patch.object(nodes, "call_one", return_value=CAND_A):
        out = nodes.generate_node({"provider": PROV, "prompt": "p", "system": "", "temperature": 0.3})
    assert out == {"candidates": [CAND_A]}
    mock_all_providers.assert_not_called()


def test_generate_node_falls_back_to_different_family_after_primary_fails():
    # Primary fails; PROV itself (same family) must be skipped in the pool;
    # the distinct-family fallback is tried next and succeeds.
    with patch.object(nodes, "all_providers", return_value=[PROV, FALLBACK_PROV]), \
         patch.object(nodes, "call_one", side_effect=[None, CAND_B]):
        out = nodes.generate_node({"provider": PROV, "prompt": "p", "system": "", "temperature": 0.3})
    assert out == {"candidates": [CAND_B]}


def test_generate_node_contributes_empty_list_when_all_fallbacks_fail():
    # Primary and every eligible fallback fail — still a clean empty list,
    # never a raised exception or a None poisoning the reducer.
    with patch.object(nodes, "all_providers", return_value=[PROV, FALLBACK_PROV]), \
         patch.object(nodes, "call_one", return_value=None):
        out = nodes.generate_node({"provider": PROV, "prompt": "p", "system": "", "temperature": 0.3})
    assert out == {"candidates": []}


def test_critique_node_scores_and_picks_winner():
    with patch.object(nodes, "select_critics", return_value=[PROV]), \
         patch.object(nodes, "call_one", return_value={"content": "[40, 91]", "provider": "c", "model": "m"}):
        out = nodes.critique_node({"candidates": [CAND_A, CAND_B], "task_description": "t"})
    assert out["scores"] == [40, 91]
    assert out["winner"] == CAND_B


def test_critique_node_short_circuits_on_single_candidate():
    # No second opinion to seek; skip the critic call entirely.
    out = nodes.critique_node({"candidates": [CAND_A], "task_description": "t"})
    assert out["winner"] == CAND_A
    assert out["scores"] == []


def test_critique_node_falls_back_to_first_when_critic_unparseable():
    with patch.object(nodes, "select_critics", return_value=[PROV]), \
         patch.object(nodes, "call_one", return_value={"content": "not json", "provider": "c", "model": "m"}):
        out = nodes.critique_node({"candidates": [CAND_A, CAND_B], "task_description": "t"})
    assert out["winner"] == CAND_A


def test_quality_gate_finalizes_when_no_violations():
    assert nodes.quality_gate({"guard_report": {"passed": True}, "repair_attempts": 0}) == "finalize"


def test_quality_gate_repairs_on_violation():
    state = {"guard_report": {"passed": False, "violations": ["fabrication"]}, "repair_attempts": 0}
    assert nodes.quality_gate(state) == "repair"


def test_quality_gate_stops_repairing_after_two_attempts():
    state = {"guard_report": {"passed": False, "violations": ["fabrication"]}, "repair_attempts": 2}
    assert nodes.quality_gate(state) == "finalize"


def test_repair_node_feeds_violations_back_into_prompt():
    state = {
        "prompt": "original",
        "guard_report": {"passed": False, "violations": ["banned phrase: leverage"]},
        "repair_attempts": 0,
    }
    out = nodes.repair_node(state)
    assert "banned phrase: leverage" in out["prompt"]
    assert "original" in out["prompt"]
    assert out["repair_attempts"] == 1
    # candidates is a reducer-backed channel (add_candidates concatenates),
    # so a plain [] would merge as a no-op rather than clearing anything.
    # repair_node must bypass the reducer with Overwrite to actually reset it.
    assert out["candidates"] == Overwrite(value=[])


# ---------------------------------------------------------------------------
# Fabrication must arm the repair loop (CI run 36651253369, case 12ed5b1de5e8).
# ---------------------------------------------------------------------------

# A realistic body (see tests/unit/realistic_resume_body.py) with Rust added to
# the Skills section. The six-one-liner version this replaced had 3 identity
# anchors; check_output for the tailor task now reads content as well as
# structure, so the clean variant below would have blocked on near_empty and
# the "no new repair rounds for honest output" claim would have been untestable.
_FABRICATING_WINNER = realistic_body().replace("Docker", "Docker, Rust")


def test_fabricated_skill_routes_guard_output_to_repair():
    """The seam that was broken, exercised end to end.

    test_quality_gate_repairs_on_violation above hand-seeds a failing
    guard_report, so it passes whatever severity fabrication carries -- it
    proves quality_gate can route to repair, never that a fabricated resume
    reaches that route. This drives the REAL guard_output_node (which calls
    the real check_output) and feeds its actual output to the real
    quality_gate. At severity "warn" this returned "finalize".
    """
    state = {
        "winner": {"content": _FABRICATING_WINNER},
        "task": "tailor",
        "base_skills": "Python, AWS, Docker",
        "repair_attempts": 0,
    }
    state.update(nodes.guard_output_node(state))

    assert state["guard_report"]["passed"] is False
    assert nodes.quality_gate(state) == "repair"


def test_repair_prompt_names_the_fabricated_skill():
    """Repair has to be informed to be worth two rounds.

    repair_node folds guard violations into the retry prompt verbatim, so the
    blocking severity is only useful if the detail string reaching the model
    identifies what was invented. A repair round that just re-rolls the dice
    would be latency with no mechanism.
    """
    state = {
        "winner": {"content": _FABRICATING_WINNER},
        "task": "tailor",
        "base_skills": "Python, AWS, Docker",
        "prompt": "Tailor this resume.",
        "repair_attempts": 0,
    }
    state.update(nodes.guard_output_node(state))
    repaired = nodes.repair_node(state)["prompt"]

    assert "Rust" in repaired
    assert "not in base resume" in repaired
    assert "Tailor this resume." in repaired


def test_clean_output_still_finalizes_on_the_first_pass():
    """No new repair rounds for honest output -- the cost control on this change."""
    state = {
        "winner": {"content": _FABRICATING_WINNER.replace(", Rust", "")},
        "task": "tailor",
        "base_skills": "Python, AWS, Docker",
        "repair_attempts": 0,
    }
    state.update(nodes.guard_output_node(state))

    assert state["guard_report"]["passed"] is True
    assert nodes.quality_gate(state) == "finalize"


# ---------------------------------------------------------------------------
# What ships when the repair budget runs out
# ---------------------------------------------------------------------------
# A bounded repair loop must terminate, so `quality_gate` finalizing
# best-effort is correct. What was not correct is that it did so SILENTLY.
# Measured 2026-10-07 over a 212-résumé batch: 87 runs (41%) logged "Repair
# budget exhausted — finalizing best-effort" and shipped with a block-severity
# violation still present, and nothing anywhere recorded which violation. A
# warning that cannot be acted on is the same class of defect as a status that
# cannot fail.


def test_exhausting_the_budget_names_what_is_shipping(caplog):
    import logging
    state = {
        "guard_report": {
            "passed": False,
            "violations": ["fabrication: 'Kotlin' not in base", "banned_phrase: 'robust'"],
            "blocking": ["fabrication: 'Kotlin' not in base"],
        },
        "repair_attempts": 2,
    }
    with caplog.at_level(logging.WARNING):
        assert nodes.quality_gate(state) == "finalize"
    text = caplog.text
    assert "Repair budget exhausted" in text
    assert "Kotlin" in text, (
        "the log says the budget is spent but not what survived — which is the "
        "state 87 of 212 résumés shipped in, unrecorded")
    assert "1 unresolved" in text, "the count of surviving blocking violations"


def test_it_falls_back_to_all_violations_when_severity_was_not_preserved(caplog):
    """An older `guard_report` has no `blocking` key, because `to_dict` used to
    discard severity. Reporting nothing for those would make the fix look like
    it had cleaned them up."""
    import logging
    state = {"guard_report": {"passed": False, "violations": ["fabrication: 'Scala'"]},
             "repair_attempts": 2}
    with caplog.at_level(logging.WARNING):
        assert nodes.quality_gate(state) == "finalize"
    assert "Scala" in caplog.text


def test_a_clean_report_still_finalizes_without_crying_wolf(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        assert nodes.quality_gate({"guard_report": {"passed": True}, "repair_attempts": 2}) == "finalize"
    assert "Repair budget exhausted" not in caplog.text
