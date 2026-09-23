from unittest.mock import patch

from agents import nodes

PROV = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
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


def test_critique_node_scores_and_picks_winner():
    with patch.object(nodes, "select_critic", return_value=PROV), \
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
    with patch.object(nodes, "select_critic", return_value=PROV), \
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
    assert out["candidates"] == []
