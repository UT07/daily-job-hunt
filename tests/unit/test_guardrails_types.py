from guardrails.policy import POLICIES, policy_for
from guardrails.types import GuardResult, Violation


def test_empty_result_passes():
    assert GuardResult.ok().passed is True


def test_result_with_blocking_violation_fails():
    r = GuardResult(violations=[Violation("fabrication", "invented AWS", "block")])
    assert r.passed is False


def test_warn_severity_does_not_fail_the_result():
    # Warnings are recorded for the eval harness but must not trigger repair.
    r = GuardResult(violations=[Violation("style", "passive voice", "warn")])
    assert r.passed is True


def test_to_dict_is_json_safe_for_graph_state():
    d = GuardResult(violations=[Violation("x", "y", "block")]).to_dict()
    assert d["passed"] is False
    assert d["violations"] == ["x: y"]


def test_policy_for_unknown_task_falls_back_to_default():
    assert policy_for("nonexistent") == policy_for("default")


def test_tailor_policy_enables_latex_checks():
    assert policy_for("tailor")["latex_structure"] is True


def test_score_policy_disables_latex_checks():
    assert policy_for("score")["latex_structure"] is False


def test_no_fairness_cap_key():
    """Regression pin: guardrails/policy.py must not carry a "fairness_cap"
    key again without also wiring something real to consume it.

    An earlier draft had one, paired with an `apply_fairness_cap` that was
    never implemented anywhere. The real geo/work-auth score cap is
    `apply_geo_score_cap` in shared/work_auth.py, called unconditionally
    from score_batch.py -- outside this package's guard pipeline entirely.
    See guardrails/policy.py's module docstring for the full explanation.
    """
    for task, policy in POLICIES.items():
        assert "fairness_cap" not in policy, (
            f"policy {task!r} carries a fairness_cap key with nothing in "
            "guardrails/ that reads it -- see guardrails/policy.py docstring"
        )
