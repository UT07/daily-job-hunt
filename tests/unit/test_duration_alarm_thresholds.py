"""A duration alarm below the function's own normal runtime can never be OK.

All four Lambda DurationP99 alarms used a flat 30,000ms against timeouts
ranging from 150s to 600s:

    generate-cover-letter   timeout 600s   alarm 30s
    tailor-resume           timeout 600s   alarm 30s
    compile-latex           timeout 150s   alarm 30s
    save-job                timeout 300s   alarm 30s

Measured 2026-09-29, generate-cover-letter over the previous week: average
88,945ms to 318,328ms, max 429,049ms. The function legitimately runs for
minutes — AI failover plus a LaTeX compile — so a 30s p99 alarm could never
reach OK while the pipeline was running. It only ever showed OK because the
metric went quiet between runs and missing data is notBreaching, so it tracked
whether the pipeline RAN rather than whether it was healthy.

That is worse than no alarm on two counts. A permanently-red alarm trains
people to ignore alarms, and these are CodeDeploy canaries — a breaching one
can roll back a perfectly healthy deploy.

Set relative to each function's own timeout instead. Above 80% means
invocations are about to start timing out, which is the actionable signal.
"""
import pathlib
import re

import pytest

TEMPLATE = pathlib.Path("template.yaml").read_text()

# (alarm logical id, function resource logical id)
ALARMS = [
    ("GenerateCoverLetterFunctionDurationP99Alarm", 600),
    ("TailorResumeFunctionDurationP99Alarm", 600),
    ("CompileLatexFunctionDurationP99Alarm", 150),
    ("SaveJobFunctionDurationP99Alarm", 300),
]


def _threshold_ms(logical: str) -> int:
    i = TEMPLATE.index(f"  {logical}:")
    block = TEMPLATE[i:i + 2000]
    return int(re.search(r"Threshold:\s*(\d+)", block).group(1))


@pytest.mark.parametrize("logical,timeout_s", ALARMS)
def test_threshold_is_below_the_function_timeout(logical, timeout_s):
    """Above the timeout the alarm is unreachable — the invocation dies first."""
    assert _threshold_ms(logical) < timeout_s * 1000


@pytest.mark.parametrize("logical,timeout_s", ALARMS)
def test_threshold_is_a_meaningful_fraction_of_the_timeout(logical, timeout_s):
    """Not a flat constant. 30s against a 600s timeout fires on every healthy
    run; the alarm must scale with what the function is actually allowed."""
    thr = _threshold_ms(logical)
    assert thr >= timeout_s * 1000 * 0.5, (
        f"{logical} at {thr}ms is under half its {timeout_s}s timeout — it will "
        "fire on normal operation"
    )


def test_no_duration_alarm_still_uses_the_flat_30s():
    stale = [lg for lg, _ in ALARMS if _threshold_ms(lg) == 30000]
    assert not stale, f"still on the flat 30s threshold: {stale}"


@pytest.mark.parametrize("logical,timeout_s", ALARMS)
def test_the_description_says_what_the_number_means(logical, timeout_s):
    """A threshold with no stated basis gets 'fixed' by raising it again."""
    i = TEMPLATE.index(f"  {logical}:")
    block = TEMPLATE[i:i + 2000]
    assert "timeout" in block.lower(), f"{logical} does not explain its threshold"
