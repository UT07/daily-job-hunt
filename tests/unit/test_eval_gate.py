from scripts.check_eval_gate import evaluate_gate

BASE = {"tier_accuracy": 0.90, "fabrication_rate": 0.04, "guard_pass_rate": 0.95}


def test_passes_when_metrics_hold():
    ok, reasons = evaluate_gate({"tier_accuracy": 0.90, "fabrication_rate": 0.04,
                                 "guard_pass_rate": 0.95}, BASE)
    assert ok and reasons == []


def test_tolerates_a_small_accuracy_dip():
    ok, _ = evaluate_gate({"tier_accuracy": 0.86, "fabrication_rate": 0.04,
                           "guard_pass_rate": 0.95}, BASE)
    assert ok is True


def test_fails_on_accuracy_drop_beyond_five_points():
    ok, reasons = evaluate_gate({"tier_accuracy": 0.84, "fabrication_rate": 0.04,
                                 "guard_pass_rate": 0.95}, BASE)
    assert ok is False
    assert any("tier_accuracy" in r for r in reasons)


def test_fails_on_any_fabrication_increase():
    ok, reasons = evaluate_gate({"tier_accuracy": 0.90, "fabrication_rate": 0.05,
                                 "guard_pass_rate": 0.95}, BASE)
    assert ok is False
    assert any("fabrication_rate" in r for r in reasons)


def test_improvement_never_fails_the_gate():
    ok, _ = evaluate_gate({"tier_accuracy": 0.99, "fabrication_rate": 0.0,
                           "guard_pass_rate": 1.0}, BASE)
    assert ok is True
