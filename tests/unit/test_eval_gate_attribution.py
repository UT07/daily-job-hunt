"""The eval gate must not report a quality regression it cannot measure.

2026-09-28: a run scored 33.3% against a 63.2% baseline and the gate said
"tier_accuracy fell 29.8%". Its p95 had fallen from 128s to 4.7s — no
OpenRouter model was reached at all, because that account's shared daily quota
was exhausted, so the council ran Groq-only. The number was real; the
attribution was wrong, and acting on it means tuning models to fix a billing
problem.
"""
import sys

sys.path.insert(0, "scripts")
from check_eval_gate import evaluate_gate, pool_shrank  # noqa: E402

BASE = {"tier_accuracy": 0.6316, "fabrication_rate": 0.0,
        "families_served": ["groq", "openrouter"]}


def test_names_the_missing_provider():
    cur = {"tier_accuracy": 0.333, "fabrication_rate": 0.0, "families_served": ["groq"]}
    assert "openrouter" in (pool_shrank(cur, BASE) or "")


def test_a_narrowed_run_is_flagged_inconclusive_but_still_fails():
    """Must still fail — an unmeasurable run merging silently is the same
    mistake as reporting SUCCEEDED on a no-op."""
    cur = {"tier_accuracy": 0.333, "fabrication_rate": 0.0, "families_served": ["groq"]}
    ok, reasons = evaluate_gate(cur, BASE)
    assert ok is False
    assert "INCONCLUSIVE" in reasons[0]
    assert "openrouter" in reasons[0]


def test_a_real_regression_on_a_full_pool_is_not_excused():
    cur = {"tier_accuracy": 0.333, "fabrication_rate": 0.0,
           "families_served": ["groq", "openrouter"]}
    ok, reasons = evaluate_gate(cur, BASE)
    assert ok is False
    assert "INCONCLUSIVE" not in reasons[0]


def test_a_wider_pool_is_not_treated_as_narrower():
    cur = {"tier_accuracy": 0.333, "fabrication_rate": 0.0,
           "families_served": ["groq", "openrouter", "qwen"]}
    assert pool_shrank(cur, BASE) is None


def test_a_baseline_without_the_field_skips_the_check():
    """Frozen before attribution existed — must not block every PR."""
    old_base = {"tier_accuracy": 0.6316, "fabrication_rate": 0.0}
    cur = {"tier_accuracy": 0.333, "fabrication_rate": 0.0, "families_served": ["groq"]}
    assert pool_shrank(cur, old_base) is None
    ok, reasons = evaluate_gate(cur, old_base)
    assert ok is False and "INCONCLUSIVE" not in reasons[0]


def test_a_healthy_run_still_passes():
    cur = {"tier_accuracy": 0.64, "fabrication_rate": 0.0,
           "families_served": ["groq", "openrouter"]}
    ok, reasons = evaluate_gate(cur, BASE)
    assert ok is True and reasons == []
