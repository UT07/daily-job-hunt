"""A guard_pass_rate failure must say what else could explain it.

On 2026-10-08 the gate failed a PR with "guard_pass_rate fell 8.0%
(92.0% -> 84.0%)" and nothing else. The PR touched neither the guardrails nor
generation. Two things explained the number:

  * the baseline was frozen 2026-09-28; the `prompt_echo` marker family landed
    2026-09-30 (#170). So 0.92 was measured by a guard SUITE that did not
    contain the marker now firing. The gate was comparing two different suites,
    not two code states -- CLAUDE.md #14, both sides of a comparison must count
    the same things.
  * the run was served by ["gemini","groq"]; the baseline by ["gemini"] alone.

tier_accuracy has carried both caveats since 2026-10-07. This branch carried
neither, even though a guard is MORE pool-sensitive than a tier score: guards
check output structure and the model that wrote the output decides it.

The verdict still FAILS in every case here. A safety metric that cannot fail
is not a safety metric; the point is that the reader is told where to look.
"""
import datetime

import pytest

from scripts.check_eval_gate import evaluate_gate

TODAY = datetime.date(2026, 10, 8)
BASE = {
    "tier_accuracy": 0.85, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
    "families_served": ["gemini"],
    "_frozen_from": "CI run 36481897936, commit f0a3648, 2026-09-28.",
}


def _run(**over):
    cur = {"tier_accuracy": 0.85, "fabrication_rate": 0.0,
           "guard_pass_rate": 0.84, "families_served": ["gemini"]}
    cur.update(over)
    return evaluate_gate(cur, BASE, {"n_cases_attempted": 25, "n_cases_total": 25})


def _guard_reason(reasons):
    hits = [r for r in reasons if r.startswith("guard_pass_rate fell")]
    assert len(hits) == 1, reasons
    return hits[0]


def test_a_guard_drop_still_fails():
    ok, reasons = _run()
    assert ok is False
    assert _guard_reason(reasons)


def test_the_reason_names_the_reference_age():
    _, reasons = _run()
    r = _guard_reason(reasons)
    assert "day(s) old" in r, r
    # The actionable instruction, not just the fact.
    assert "git log -S" in r, r


def test_the_reason_names_a_changed_pool():
    _, reasons = _run(families_served=["gemini", "groq"])
    r = _guard_reason(reasons)
    assert "gemini" in r and "groq" in r, r
    assert "output structure" in r, r


def test_an_identical_pool_is_not_mentioned():
    # Noise in a failure message is how a reader learns to skim it.
    _, reasons = _run(families_served=["gemini"])
    assert "gained" not in _guard_reason(reasons)


def test_an_undated_baseline_says_nothing_about_age():
    undated = {k: v for k, v in BASE.items() if k != "_frozen_from"}
    ok, reasons = evaluate_gate(
        {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "guard_pass_rate": 0.84,
         "families_served": ["gemini"]},
        undated, {"n_cases_attempted": 25, "n_cases_total": 25})
    assert ok is False
    assert "day(s) old" not in _guard_reason(reasons)


def test_a_guard_rise_is_never_a_failure():
    ok, reasons = _run(guard_pass_rate=0.99)
    assert ok is True, reasons


def test_a_drop_inside_tolerance_passes():
    # GUARD_TOLERANCE is 0.02 and deliberately tighter than the tier one.
    ok, _ = _run(guard_pass_rate=0.91)
    assert ok is True


def test_a_baseline_without_the_field_skips_the_check():
    """A baseline frozen before guard_pass_rate existed must not block every
    PR -- the same rule pool_shrank follows for families_served."""
    older = {k: v for k, v in BASE.items() if k != "guard_pass_rate"}
    ok, reasons = evaluate_gate(
        {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "guard_pass_rate": 0.10,
         "families_served": ["gemini"]},
        older, {"n_cases_attempted": 25, "n_cases_total": 25})
    assert ok is True, reasons


def test_the_fabrication_check_is_untouched_by_any_of_this():
    """Fabrication has no tolerance and no caveats, by design: any rise fails."""
    ok, reasons = _run(guard_pass_rate=0.92, fabrication_rate=0.01)
    assert ok is False
    assert any("fabrication_rate rose" in r for r in reasons), reasons
