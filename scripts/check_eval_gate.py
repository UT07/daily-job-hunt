"""Compare an eval report against the committed baseline.

Thresholds come from the spec (docs/superpowers/plans/2026-09-22-ey-genai-
platform-upgrade.md, Global Constraints): more than 5 percentage points of
tier accuracy lost, or any rise in fabrication, fails the build.

This is deliberately dependency-free (stdlib only) and reads nothing but two
JSON files, so it can be exercised standalone without the AI harness for a
regression drill — see the "red-then-green" run in
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-23-25-report.md.
"""
import json
import pathlib
import sys

ACCURACY_TOLERANCE = 0.05


def evaluate_gate(current: dict, baseline: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    drop = baseline["tier_accuracy"] - current["tier_accuracy"]
    if drop > ACCURACY_TOLERANCE:
        reasons.append(
            f"tier_accuracy fell {drop:.1%} "
            f"({baseline['tier_accuracy']:.1%} -> {current['tier_accuracy']:.1%})"
        )

    if current["fabrication_rate"] > baseline["fabrication_rate"]:
        reasons.append(
            f"fabrication_rate rose "
            f"({baseline['fabrication_rate']:.1%} -> {current['fabrication_rate']:.1%})"
        )

    return (not reasons), reasons


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    current = json.loads((root / "evals/report.json").read_text())["summary"]
    baseline = json.loads((root / "evals/baseline.json").read_text())

    ok, reasons = evaluate_gate(current, baseline)
    print(json.dumps({"current": current, "baseline": baseline}, indent=2))
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
