from evals import metrics

RESULTS = [
    {"id": "a", "task": "score", "expected_tier": "S", "actual_tier": "S",
     "guards_passed": True, "fabricated": False, "latency_s": 1.0, "scores": [90, 90, 90]},
    {"id": "b", "task": "score", "expected_tier": "A", "actual_tier": "B",
     "guards_passed": True, "fabricated": False, "latency_s": 2.0, "scores": [70, 80, 90]},
    {"id": "c", "task": "tailor", "expected_tier": None, "actual_tier": None,
     "guards_passed": False, "fabricated": True, "latency_s": 3.0, "scores": []},
]


def test_tier_accuracy_counts_adjacent_tiers_as_correct():
    # A and B are adjacent; the spec allows one tier of tolerance.
    assert metrics.tier_accuracy(RESULTS) == 1.0


def test_tier_accuracy_penalises_a_two_tier_miss():
    off = [{**RESULTS[1], "actual_tier": "D"}]
    assert metrics.tier_accuracy(off) == 0.0


def test_fabrication_rate():
    assert metrics.fabrication_rate(RESULTS) == 1 / 3


def test_guard_pass_rate():
    assert metrics.guard_pass_rate(RESULTS) == 2 / 3


def test_score_variance_is_zero_for_identical_repeats():
    assert metrics.score_variance([RESULTS[0]]) == 0.0


def test_score_variance_detects_spread():
    assert metrics.score_variance([RESULTS[1]]) > 0


def test_latency_percentiles():
    p = metrics.latency_percentiles(RESULTS)
    assert p["p50"] == 2.0


def test_summarise_returns_every_gated_metric():
    s = metrics.summarise(RESULTS)
    assert {"tier_accuracy", "fabrication_rate", "guard_pass_rate",
            "score_variance", "p50", "p95", "n"} <= set(s)
