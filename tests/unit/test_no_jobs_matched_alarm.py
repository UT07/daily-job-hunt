"""The no-jobs-matched alarm must judge days the pipeline ran, not weekends.

The daily schedule is cron(0 7 ? * MON-FRI *). The alarm summed JobsMatched
over 86400s with TreatMissingData: breaching, so every Saturday and Sunday —
no run, no datapoint — read as "matched 0 jobs" and paged. An alarm that
fires every weekend trains its reader to ignore it, which is how the real
zero-match day gets missed.

notBreaching is safe only because a run that reaches SavePipelineMetrics
ALWAYS emits a JobsMatched datapoint, including Value 0. That premise is
asserted below against save_metrics itself, so if the emission ever becomes
conditional this test fails rather than the alarm going quiet.

What notBreaching gives up — a weekday where the schedule never fired — is
covered by DailyPipelineNoArtifactsAlarm (still breaching on missing data)
and a run that dies early by the ExecutionsFailed alarm.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit.test_deploy_path_parity import _load_template_tolerant_of_cfn_tags

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))


def _resources():
    return _load_template_tolerant_of_cfn_tags()["Resources"]


def test_daily_schedule_skips_weekends():
    # The premise of the fix; if the schedule goes 7-day, revisit the alarm.
    expr = _resources()["DailyPipelineSchedule"]["Properties"]["ScheduleExpression"]
    assert "MON-FRI" in expr, expr


def test_no_jobs_matched_alarm_does_not_treat_a_weekend_as_zero():
    props = _resources()["DailyPipelineNoJobsMatchedAlarm"]["Properties"]
    assert props["MetricName"] == "JobsMatched"
    assert props["TreatMissingData"] == "notBreaching", (
        "missing data on a MON-FRI schedule is every weekend; breaching pages "
        "every Saturday and Sunday"
    )
    assert props["ComparisonOperator"] == "LessThanThreshold" and props["Threshold"] == 1


def test_a_run_that_matched_nothing_still_emits_a_zero_datapoint():
    import save_metrics

    cw = MagicMock()
    with patch("save_metrics._get_cloudwatch", return_value=cw):
        save_metrics._emit_cloudwatch_metrics(
            {"resumes": 0, "cover_letters": 0, "resume_failures": 0}, 0)
    data = cw.put_metric_data.call_args.kwargs["MetricData"]
    matched = [d for d in data if d["MetricName"] == "JobsMatched"]
    assert matched and matched[0]["Value"] == 0, (
        "JobsMatched=0 must be emitted, or notBreaching hides zero-match days"
    )
