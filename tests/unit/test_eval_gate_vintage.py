"""A gate that reports a number without saying what it compares against.

On 2026-10-07 the AI Eval Gate failed a PR with "tier_accuracy fell 10.0%
(85.0% -> 75.0%)". The PR could not have caused it — the eval calls
score_batch.score_single_job directly and the PR touched only `handler` — but
nothing in the output said how old the reference was, so the only two available
responses were to ignore a red gate or to re-freeze the baseline. Both are
worse than the drift.

tier_accuracy is the sharpest case: evals/harness.py records that the golden
set's expected tiers came from this system's own production scores, so the
metric measures agreement with a frozen snapshot of the model's own opinions.
That reference ages, and the percentage gives no hint that it does.

Same shape as `pool_shrank`, which already exists here for the same reason: a
comparison is only meaningful between comparable sides, and the gate should say
when they are not.
"""
import datetime
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from check_eval_gate import baseline_age_days, evaluate_gate  # noqa: E402

BASE = {"tier_accuracy": 0.85, "fabrication_rate": 0.0, "guard_pass_rate": 0.92,
        "_frozen_from": "CI run 36481897936, commit f0a3648, 2026-09-28."}


def test_the_age_is_read_from_the_freeze_note():
    age = baseline_age_days(BASE, today=datetime.date(2026, 10, 7))
    assert age == 9


def test_an_undated_baseline_returns_none_rather_than_guessing():
    assert baseline_age_days({"_frozen_from": "some CI run"}) is None
    assert baseline_age_days({}) is None


def test_a_tier_accuracy_fall_names_the_reference_and_its_age():
    cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92}
    ok, reasons = evaluate_gate(cur, BASE)
    assert ok is False, "a 10-point fall must still fail"
    blob = " ".join(reasons)
    assert "day(s) old" in blob, "the reference's age is not stated"
    assert "OWN earlier outputs" in blob, (
        "the message does not say the reference is the system's own output")


def test_it_still_fails_rather_than_excusing_itself():
    """Naming the cause must not become a way to pass. An unmeasurable run that
    merges silently is the same defect as reporting SUCCEEDED on a no-op."""
    cur = {"tier_accuracy": 0.10, "fabrication_rate": 0.0, "guard_pass_rate": 0.92}
    ok, _ = evaluate_gate(cur, BASE)
    assert ok is False


def test_the_message_distinguishes_re_measuring_from_re_freezing():
    """Re-measuring on main is a legitimate response to a stale reference.
    Re-freezing to turn the gate green is how the signal gets destroyed."""
    cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92}
    _, reasons = evaluate_gate(cur, BASE)
    blob = " ".join(reasons).lower()
    assert "re-measuring" in blob and "re-freezing" in blob


def test_a_pass_is_unaffected():
    cur = {"tier_accuracy": 0.86, "fabrication_rate": 0.0, "guard_pass_rate": 0.93}
    ok, reasons = evaluate_gate(cur, BASE)
    assert ok is True and reasons == []
