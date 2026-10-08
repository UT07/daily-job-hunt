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


# ---------------------------------------------------------------------------
# A run that measured nothing must not report a quality verdict
#
# Measured 2026-10-07: the AI Eval Gate job hit its `timeout-minutes: 20`
# ceiling twice and was CANCELLED before the harness wrote a report at all.
# The check then showed "AI Eval Gate: fail" — a quality verdict for a run that
# measured nothing, which is the same class of lie as reporting SUCCEEDED on a
# no-op. The harness now stops itself before the ceiling and writes a partial
# report; the gate reads how much of the golden set that report covers.
# ---------------------------------------------------------------------------
import ast  # noqa: E402

from check_eval_gate import incomplete_run  # noqa: E402

FULL = {"tier_accuracy": 0.86, "fabrication_rate": 0.0, "guard_pass_rate": 0.93}


def test_a_partial_run_is_detected():
    assert incomplete_run({"n_cases_attempted": 18, "n_cases_total": 26})
    assert "18 of 26" in incomplete_run({"n_cases_attempted": 18, "n_cases_total": 26})


def test_a_complete_run_is_not_flagged():
    assert incomplete_run({"n_cases_attempted": 26, "n_cases_total": 26}) is None


def test_missing_coverage_fields_do_not_guess():
    """A report predating these fields must not be read as partial."""
    assert incomplete_run({}) is None
    assert incomplete_run({"n_cases_attempted": 18}) is None


def test_a_partial_run_fails_but_is_labelled_inconclusive():
    """It must not merge silently — and it must not be read as a regression."""
    ok, reasons = evaluate_gate(FULL, BASE, {"n_cases_attempted": 18, "n_cases_total": 26})
    assert ok is False, "a run that measured 18 of 26 cases must not pass"
    blob = " ".join(reasons)
    assert "INCONCLUSIVE" in blob and "not a quality verdict" in blob
    assert "do not re-freeze the baseline from a partial run" in blob


def test_a_complete_passing_run_is_unaffected_by_the_new_check():
    ok, reasons = evaluate_gate(FULL, BASE, {"n_cases_attempted": 26, "n_cases_total": 26})
    assert ok is True and reasons == []


def test_the_harness_checks_its_deadline_before_doing_case_work():
    """A deadline evaluated after the expensive call would never save a run."""
    src = pathlib.Path("evals/harness.py").read_text()
    tree = ast.parse(src)
    loop = None
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) \
                and getattr(node.iter.func, "id", "") == "enumerate":
            body = ast.get_source_segment(src, node) or ""
            if "checkpointed" in body:
                loop = node
                break
    assert loop is not None, "the golden-set loop was not found"
    first = ast.get_source_segment(src, loop.body[0]) or ""
    assert "_deadline_s" in first, (
        "the deadline is not the first thing the loop checks, so a run can "
        "still be killed mid-case and write no report"
    )
    assert "EVAL_DEADLINE_S" in src


# ---------------------------------------------------------------------------
# A prescribed remedy that the configuration forbids is not a remedy
# ---------------------------------------------------------------------------
# The failure message tells the reader "re-measuring the baseline on main is a
# legitimate response; re-freezing it to turn this green is not."
#
# Until 2026-10-08 that was impossible. `detect-ai-changes` in ci.yml returns
# ai_relevant=false for every event that is not a `pull_request` — deliberately,
# to protect free-tier provider quota — so the eval gate had never run on main
# and could not be made to. The only ACHIEVABLE action was the one the message
# forbids, which makes the advice worse than none: it reads as a procedure
# while leaving re-freezing as the single thing anyone can actually do.
#
# ci.yml now has a `workflow_dispatch` with a `run_eval` input for exactly this.
# These tests tie the two together so the message cannot outlive the mechanism.

def test_the_message_tells_the_reader_how_to_re_measure():
    cur = {"tier_accuracy": 0.75, "fabrication_rate": 0.0, "guard_pass_rate": 0.92}
    _, reasons = evaluate_gate(cur, BASE)
    blob = " ".join(reasons)
    assert "workflow_dispatch" in blob and "run_eval" in blob, (
        "the message prescribes re-measuring on main without saying how; the "
        "gate does not run on main automatically, so 'how' is the whole advice"
    )


def test_the_workflow_can_actually_do_what_the_message_prescribes():
    """The other half, and the half that was missing. Asserted against ci.yml
    itself rather than against the message, because the message being right is
    worthless if the workflow cannot honour it."""
    ci = (_ROOT / ".github/workflows/ci.yml").read_text()
    triggers = ci.split("jobs:", 1)[0]
    assert "workflow_dispatch" in triggers, (
        "ci.yml has no manual trigger, so the eval gate cannot be run on main "
        "on demand and the baseline can never be legitimately re-measured"
    )
    assert "run_eval" in triggers, "workflow_dispatch has no run_eval input"
    assert 'if [ "$EVENT_NAME" = "workflow_dispatch" ]' in ci, (
        "detect-ai-changes does not special-case workflow_dispatch, so a manual "
        "run still reports ai_relevant=false and the gate still skips"
    )
