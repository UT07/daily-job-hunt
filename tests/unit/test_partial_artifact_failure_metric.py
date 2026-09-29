"""Alarms caught total absence, never partial failure.

2026-09-29: the daily run matched 10 jobs, compiled 8 resumes, and reported
SUCCEEDED. Two S/A-tier jobs — the best of the day — silently had no resume.
Nothing fired:

    naukribaba-daily-pipeline-no-artifacts-24h   ArtifactsCompiled < 1  -> 8, OK
    naukribaba-daily-pipeline-no-jobs-matched-24h  JobsMatched  < 1     -> 10, OK
    naukribaba-daily-pipeline-executions-failed  ExecutionsFailed > 0   -> 0, OK

The last one stayed quiet for a real reason: compile_latex does not RAISE on a
LaTeX error, it returns {"error": "compilation_failed", ...}. Step Functions
sees a successful Task, so no Catch fires, NotifyError never runs, and the
Fail state added for the earlier silent-success fix never ticks.

So "did every matched job get its artifact?" was measured by nothing. A run
that loses 20% of its output is indistinguishable from a clean one.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "lambdas/pipeline")
import save_metrics  # noqa: E402


def _saved(has_resume, failed=False):
    return {"job_hash": "h", "user_id": "u", "saved": True,
            "has_resume": has_resume, "failed": failed}


def test_failures_are_counted_not_just_successes():
    counts = save_metrics._count_compiled_artifacts(
        [_saved(True), _saved(True), _saved(False, failed=True), _saved(False)]
    )
    assert counts["resumes"] == 2
    assert counts["resume_failures"] == 2, (
        "a run that lost half its resumes still reported only the successes"
    )


def test_a_clean_run_reports_zero_failures():
    counts = save_metrics._count_compiled_artifacts([_saved(True), _saved(True)])
    assert counts["resumes"] == 2 and counts["resume_failures"] == 0


def test_an_empty_run_is_not_a_failure():
    """No matched jobs means nothing to compile — that is the no-jobs-matched
    alarm's business, not this one's."""
    counts = save_metrics._count_compiled_artifacts([])
    assert counts["resume_failures"] == 0


def test_the_failure_count_is_emitted_to_cloudwatch():
    cw = MagicMock()
    with patch.object(save_metrics, "_get_cloudwatch", return_value=cw):
        save_metrics._emit_cloudwatch_metrics(
            {"resumes": 8, "cover_letters": 6, "resume_failures": 2}, matched_count=10
        )
    data = cw.put_metric_data.call_args.kwargs["MetricData"]
    by_name = {m["MetricName"]: m for m in data}
    assert "ResumeCompileFailures" in by_name, "nothing measures the shortfall"
    assert by_name["ResumeCompileFailures"]["Value"] == 2


def test_a_zero_is_still_emitted_so_the_alarm_can_leave_ALARM_state():
    """A metric that only reports on failure leaves the alarm stuck.

    CloudWatch needs the datapoint to go back to 0 to return to OK; with
    missing data it stays in ALARM (or INSUFFICIENT_DATA) after one bad run.
    """
    cw = MagicMock()
    with patch.object(save_metrics, "_get_cloudwatch", return_value=cw):
        save_metrics._emit_cloudwatch_metrics(
            {"resumes": 10, "cover_letters": 6, "resume_failures": 0}, matched_count=10
        )
    data = cw.put_metric_data.call_args.kwargs["MetricData"]
    by_name = {m["MetricName"]: m for m in data}
    assert by_name["ResumeCompileFailures"]["Value"] == 0


def test_emission_failure_still_does_not_break_the_pipeline():
    cw = MagicMock()
    cw.put_metric_data.side_effect = RuntimeError("cloudwatch down")
    with patch.object(save_metrics, "_get_cloudwatch", return_value=cw):
        save_metrics._emit_cloudwatch_metrics(
            {"resumes": 1, "cover_letters": 0, "resume_failures": 0}, matched_count=1
        )  # must not raise
