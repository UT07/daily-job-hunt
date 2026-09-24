from typing import get_type_hints

from agents.state import Candidate, CouncilState, add_candidates


def test_candidate_matches_call_provider_return_shape():
    # _call_provider returns {"content", "provider", "model"} — Candidate must
    # mirror it exactly so nodes can pass results through untranslated.
    assert set(get_type_hints(Candidate)) == {"content", "provider", "model"}


def test_add_candidates_concatenates():
    a = [{"content": "x", "provider": "groq", "model": "m1"}]
    b = [{"content": "y", "provider": "openrouter", "model": "m2"}]
    assert add_candidates(a, b) == a + b


def test_add_candidates_tolerates_empty_branch():
    # A generator branch that failed contributes an empty list, not None.
    a = [{"content": "x", "provider": "groq", "model": "m1"}]
    assert add_candidates(a, []) == a


def test_council_state_has_required_keys():
    keys = set(get_type_hints(CouncilState))
    assert {
        "task", "prompt", "system", "task_description",
        "candidates", "scores", "winner", "guard_report",
        "repair_attempts", "trace_id",
        # Written by plan_node, read by the fan-out router.
        "generators",
        # Read by guard_output_node for tailoring tasks.
        "base_skills", "base_body", "header_markers",
    } <= keys
