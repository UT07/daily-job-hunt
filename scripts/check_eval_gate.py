"""Compare an eval report against the committed baseline.

Thresholds come from the spec (docs/superpowers/plans/2026-09-22-ey-genai-
platform-upgrade.md, Global Constraints): more than 5 percentage points of
tier accuracy lost, or any rise in fabrication, fails the build.

This is deliberately dependency-free (stdlib only) and reads nothing but two
JSON files, so it can be exercised standalone without the AI harness for a
regression drill — see the "red-then-green" run in
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-23-25-report.md.
"""
import datetime
import json
import pathlib
import re
import sys

ACCURACY_TOLERANCE = 0.05

# Tighter than ACCURACY_TOLERANCE, deliberately. A tier score is a sampled
# judgement -- the same job scored 25 to 80 across 30 verified models -- so its
# tolerance exists to absorb real noise. A guard is a deterministic check that
# fired or did not; the only thing this slack absorbs is the golden set's case
# mix shifting between runs.
GUARD_TOLERANCE = 0.02


_FROZEN_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def baseline_age_days(baseline: dict, today: datetime.date | None = None) -> int | None:
    """How old the thing we are comparing against is, in days.

    Every metric here is a comparison, and a comparison has two sides. The
    gate has always reported one of them. `tier_accuracy` in particular
    compares against labels that are THIS SYSTEM'S OWN earlier outputs
    (evals/harness.py says so), so what it measures is agreement with a
    frozen snapshot of the model's opinions -- and that reference ages.

    On 2026-10-07 the gate failed a PR with "tier_accuracy fell 10.0%" against
    a baseline frozen nine days earlier. The PR could not have caused it: the
    eval calls score_single_job directly and the PR touched only handler. The
    number was real and the attribution was impossible, because nothing printed
    how old the reference was. A number that silently decays is how a team
    ends up either ignoring a red gate or re-freezing it, and both are worse
    than the drift.

    Returns None when the baseline carries no date, rather than guessing.
    """
    m = _FROZEN_DATE.search(str(baseline.get("_frozen_from", "")))
    if not m:
        return None
    frozen = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return ((today or datetime.date.today()) - frozen).days

def pool_shrank(current: dict, baseline: dict) -> str | None:
    """Whether this run was served by a narrower provider pool than the baseline.

    A quality comparison is only meaningful between comparable pools. On
    2026-09-28 a run scored 33.3% against a 63.2% baseline while its p95 fell
    from 128s to 4.7s: no OpenRouter model had been reached at all, because
    that account's shared daily quota was exhausted. The gate reported a
    quality regression. What it had measured was provider availability.

    Both sides need the field, so a baseline frozen before it existed simply
    skips the check rather than blocking every PR.
    """
    now = current.get("families_served")
    was = baseline.get("families_served")
    if not now or not was:
        return None
    missing = sorted(set(was) - set(now))
    if missing:
        return (f"served by {len(now)} provider(s) {now} vs baseline's "
                f"{len(was)} {was} — missing {missing}")
    return None


def incomplete_run(report: dict) -> str | None:
    """Whether this run measured the whole golden set.

    A run that covered 18 of 26 cases produces metrics over a DIFFERENT
    population from the baseline's, so comparing them is not a quality
    judgement — the same reasoning as `pool_shrank` for providers.

    Measured 2026-10-07: the gate job hit its 20-minute ceiling twice and was
    cancelled before the harness wrote a report at all, and the check reported
    "fail" — a quality verdict for a run that measured nothing. The harness now
    stops itself and writes a partial report; this is the half that reads it.
    """
    attempted = report.get("n_cases_attempted")
    total = report.get("n_cases_total")
    if attempted is None or total is None or attempted >= total:
        return None
    return f"covered {attempted} of {total} golden case(s)"


def evaluate_gate(current: dict, baseline: dict,
                  report: dict | None = None) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    partial = incomplete_run(report or {})
    if partial:
        # Still a failure. An unmeasurable run must not merge silently, which
        # is the same mistake as reporting SUCCEEDED on a no-op. But it must
        # not be read as a quality regression either.
        reasons.append(
            f"INCONCLUSIVE, not a quality verdict: this run {partial}, so its "
            f"metrics describe a different population from the baseline's. "
            f"The usual cause is the harness hitting EVAL_DEADLINE_S. Re-run, "
            f"or raise the job's timeout; do not re-freeze the baseline from a "
            f"partial run."
        )

    drop = baseline["tier_accuracy"] - current["tier_accuracy"]
    if drop > ACCURACY_TOLERANCE:
        narrowed = pool_shrank(current, baseline)
        if narrowed:
            # Still a failure — an unmeasurable run must not merge silently,
            # which is the same mistake as reporting SUCCEEDED on a no-op. But
            # naming the cause stops the next reader "fixing" a quality
            # regression that was really an exhausted quota.
            reasons.append(
                f"INCONCLUSIVE, not necessarily a regression: tier_accuracy fell {drop:.1%} "
                f"({baseline['tier_accuracy']:.1%} -> {current['tier_accuracy']:.1%}), but the run was "
                f"{narrowed}. Re-run once quota resets, or give the council a family on "
                f"independent billing, before treating this as a quality change."
            )
        else:
            age = baseline_age_days(baseline)
            vintage = (f" The reference is {age} day(s) old, and tier_accuracy "
                       f"measures agreement with THIS SYSTEM'S OWN earlier "
                       f"outputs (evals/harness.py), so part of any fall may be "
                       f"the reference drifting rather than this change. "
                       f"Re-measuring the baseline on main is a legitimate "
                       f"response; re-freezing it to turn this green is not."
                       ) if age is not None else ""
            reasons.append(
                f"tier_accuracy fell {drop:.1%} "
                f"({baseline['tier_accuracy']:.1%} -> {current['tier_accuracy']:.1%})."
                + vintage
            )

    # guard_pass_rate covers the guardrails layer -- injection detection and
    # output guards. It was computed and reported from the beginning and gated
    # by nothing: on 2026-09-28 it fell 1.00 -> 0.92 across a passing run and
    # no check looked at it. A safety metric nobody reads is not a safety
    # metric. Both sides must carry the field, so a baseline frozen before it
    # existed skips the check rather than blocking every PR.
    guard_now = current.get("guard_pass_rate")
    guard_was = baseline.get("guard_pass_rate")
    if guard_now is not None and guard_was is not None:
        guard_drop = guard_was - guard_now
        if guard_drop > GUARD_TOLERANCE:
            reasons.append(
                f"guard_pass_rate fell {guard_drop:.1%} "
                f"({guard_was:.1%} -> {guard_now:.1%}) — the guardrails layer let "
                f"more through than the baseline run did"
            )

    if current["fabrication_rate"] > baseline["fabrication_rate"]:
        reasons.append(
            f"fabrication_rate rose "
            f"({baseline['fabrication_rate']:.1%} -> {current['fabrication_rate']:.1%})"
        )

    return (not reasons), reasons


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    report = json.loads((root / "evals/report.json").read_text())
    current = report["summary"]
    baseline = json.loads((root / "evals/baseline.json").read_text())

    ok, reasons = evaluate_gate(current, baseline, report)
    print(json.dumps({"current": current, "baseline": baseline}, indent=2))
    # Printed on PASS as well as FAIL, deliberately. The age is a property of
    # every comparison this script makes, not a detail of its failures, and a
    # reader who only ever sees it beside a red gate will read it as an excuse.
    age = baseline_age_days(baseline)
    print(f"baseline reference: {age} day(s) old"
          if age is not None else
          "baseline reference: undated — add a date to _frozen_from")
    if ok:
        print("EVAL GATE: PASS")
        return 0
    print("EVAL GATE: FAIL")
    for reason in reasons:
        print(f"  - {reason}")
    print("\nIf this change is a deliberate quality tradeoff, update "
          "evals/baseline.json in this PR and say why in the description.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
