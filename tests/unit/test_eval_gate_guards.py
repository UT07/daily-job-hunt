"""The gate watched quality and ignored safety.

evaluate_gate checked tier_accuracy and fabrication_rate. guard_pass_rate --
the guardrails layer's own pass rate, covering injection detection and output
guards -- was computed, reported, and gated by nothing.

Observed 2026-09-28: guard_pass_rate went 1.00 -> 0.92 between the baseline and
a passing CI run. Nothing flagged it. A safety metric that degrades silently is
the same failure shape as a pipeline reporting SUCCEEDED having written nothing:
the number was right there in the report and no one was looking at it.

Also here, as coverage rather than repair: pool_shrank already compares WHICH
families served a run (set difference), not merely how many. That matters more
once the baseline is re-frozen from a Gemini-served run, because a later
Groq-served run has the same family count -- and nothing pinned that behaviour
before, so it could have been "simplified" into a length check.
"""
import sys

sys.path.insert(0, "scripts")
from check_eval_gate import evaluate_gate, pool_shrank  # noqa: E402

BASE = {
    "tier_accuracy": 0.85, "fabrication_rate": 0.0,
    "guard_pass_rate": 0.92, "families_served": ["gemini"],
}


def _cur(**over):
    return {**BASE, **over}


# ---------------------------------------------------------------------------
# guard_pass_rate is a safety metric and must be gated
# ---------------------------------------------------------------------------

def test_a_guard_regression_fails_the_gate():
    ok, reasons = evaluate_gate(_cur(guard_pass_rate=0.80), BASE)
    assert not ok
    assert any("guard" in r.lower() for r in reasons), reasons


def test_a_small_guard_dip_is_tolerated():
    """Some slack, because the golden set's case mix shifts between runs.

    Not much slack: a guard either fired or it did not, so unlike a score there
    is no sampling noise for a tolerance to absorb.
    """
    ok, _ = evaluate_gate(_cur(guard_pass_rate=0.91), BASE)
    assert ok


def test_improving_guards_never_fails():
    ok, _ = evaluate_gate(_cur(guard_pass_rate=1.0), BASE)
    assert ok


def test_a_report_without_the_field_skips_the_check():
    """Backwards compatible: a baseline frozen before this existed still works."""
    cur = {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "families_served": ["gemini"]}
    base = {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "families_served": ["gemini"]}
    ok, reasons = evaluate_gate(cur, base)
    assert ok, reasons


def test_guards_are_held_to_a_tighter_bar_than_accuracy():
    """A drop that accuracy would tolerate must still fail for guards.

    Scoring is inherently noisy -- the same job scored 25 to 80 across 30
    models -- so accuracy carries a 5-point tolerance. A guard is a
    deterministic check: it fired or it did not. There is no sampling noise for
    a tolerance to absorb, so granting guards the same 5-point slack would let
    real safety erosion through under cover of the allowance made for noise.
    """
    dip = 0.04
    assert evaluate_gate(_cur(tier_accuracy=BASE["tier_accuracy"] - dip), BASE)[0], \
        "accuracy should tolerate a 4-point dip"
    ok, _ = evaluate_gate(_cur(guard_pass_rate=BASE["guard_pass_rate"] - dip), BASE)
    assert not ok, "guards must NOT tolerate the same 4-point dip"


# ---------------------------------------------------------------------------
# pool_shrank must compare WHICH families, not just how many
# ---------------------------------------------------------------------------

def test_a_different_single_family_is_not_a_like_for_like_run():
    """Baseline served by gemini, run served by groq: same count, different pool.

    Comparing their tier_accuracy measures the two providers against each
    other, not the change under review.
    """
    assert pool_shrank({"families_served": ["groq"]}, {"families_served": ["gemini"]})


def test_the_same_family_is_comparable():
    assert pool_shrank({"families_served": ["gemini"]}, {"families_served": ["gemini"]}) is None


def test_a_superset_is_comparable():
    """More providers than the baseline is not a narrower run."""
    assert pool_shrank(
        {"families_served": ["gemini", "groq"]}, {"families_served": ["gemini"]},
    ) is None


def test_a_disjoint_pool_is_named_in_the_reason():
    ok, reasons = evaluate_gate(
        {"tier_accuracy": 0.60, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
         "families_served": ["groq"]},
        BASE,
    )
    assert not ok
    joined = " ".join(reasons)
    assert "INCONCLUSIVE" in joined
    assert "groq" in joined and "gemini" in joined
