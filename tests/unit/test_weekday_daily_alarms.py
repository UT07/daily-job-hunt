"""The daily "did the pipeline produce anything" alarms judge weekdays only.

The schedule is cron(0 7 ? * MON-FRI *). DailyPipelineNoArtifactsAlarm and
DailyPipelineNoJobsMatchedAlarm alarm on a 24h Sum < 1. A plain metric alarm
can only choose what missing data means:

  * breaching    -> every Saturday and Sunday pages (no run, no datapoint)
  * notBreaching -> a weekday on which the schedule never fired is silent

Both are wrong, so each alarm is a metric-math expression that decides per
window whether the window should hold a run:

  IF(HOUR(m1) < 12 OR DAY(m1) == 5 OR DAY(m1) == 6, 1, FILL(m1, 0))

CloudWatch alarms evaluate a SLIDING window ("the boundaries of the window are
not aligned to the wall clock"), and the datapoint is stamped with the
window's start. A window starting at or after 12:00 UTC on day D holds exactly
D+1's 07:00 run, so it is judged only when D+1 is a weekday.

This file pins the template shape AND evaluates the template's own expression
string with a small interpreter of the documented semantics over a synthetic
week, so changing the expression in template.yaml is what the simulation
tests -- not a copy of it here.
"""
from __future__ import annotations

import functools
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.test_deploy_path_parity import _load_template_tolerant_of_cfn_tags

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

WEEKDAY_ALARMS = {"DailyPipelineNoArtifactsAlarm", "DailyPipelineNoJobsMatchedAlarm"}
DAY_SECONDS = 86400


@functools.lru_cache(maxsize=1)
def _resources():
    return _load_template_tolerant_of_cfn_tags()["Resources"]


def _alarm_period(props):
    if "Period" in props:
        return props["Period"]
    periods = {m["MetricStat"]["Period"] for m in props.get("Metrics", []) if "MetricStat" in m}
    return periods.pop() if len(periods) == 1 else None


def _returned_expression(props):
    returned = [m for m in props.get("Metrics", []) if m.get("ReturnData", True)]
    assert len(returned) == 1, "an alarm on metric math must return exactly one series"
    return returned[0]["Expression"]


def _schedule_hour_minute():
    expr = _resources()["DailyPipelineSchedule"]["Properties"]["ScheduleExpression"]
    m = re.fullmatch(r"cron\((\d+) (\d+) \? \* MON-FRI \*\)", expr)
    assert m, f"the weekday expression assumes a once-a-day MON-FRI schedule, got {expr}"
    return int(m.group(2)), int(m.group(1))


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_every_daily_absence_alarm_is_weekday_aware():
    """Every 24h alarm that fires on a LOW value watches a MON-FRI metric, and
    must be one of the weekday-aware alarms. (A GreaterThan alarm such as
    ResumeCompileFailures is unaffected: an empty weekend is not a breach.)"""
    found = {
        name for name, r in _resources().items()
        if isinstance(r, dict) and r.get("Type") == "AWS::CloudWatch::Alarm"
        and _alarm_period(r["Properties"]) == DAY_SECONDS
        and r["Properties"]["ComparisonOperator"].startswith("LessThan")
    }
    assert found == WEEKDAY_ALARMS


@pytest.mark.parametrize("name", sorted(WEEKDAY_ALARMS))
def test_alarm_uses_the_weekday_expression_and_an_empty_weekday_breaches(name):
    props = _resources()[name]["Properties"]
    assert "MetricName" not in props and "Statistic" not in props, "must be metric math"
    (m1,) = [m for m in props["Metrics"] if m["Id"] == "m1"]
    assert m1["ReturnData"] is False
    assert m1["MetricStat"]["Period"] == DAY_SECONDS and m1["MetricStat"]["Stat"] == "Sum"

    expr = _returned_expression(props)
    for fn in ("FILL(m1, 0)", "DAY(m1)", "HOUR(m1)", "IF("):
        assert fn in expr, f"{name}: expression lacks {fn}: {expr}"

    # FILL makes an empty weekday 0; 0 must be a breach and 1 must not.
    assert props["ComparisonOperator"] == "LessThanThreshold"
    assert props["Threshold"] == 1
    assert props["EvaluationPeriods"] == 1
    # The expression is non-sparse, so missing data means the expression
    # broke. That must page, not go quiet.
    assert props["TreatMissingData"] == "breaching"


def test_both_alarms_use_the_same_expression():
    exprs = {_returned_expression(_resources()[n]["Properties"]) for n in WEEKDAY_ALARMS}
    assert len(exprs) == 1, exprs


def test_a_run_that_matched_nothing_still_emits_a_zero_datapoint():
    # Not required for correctness any more (FILL covers a missing point),
    # but a zero-match run must remain distinguishable from no run at all in
    # the raw metric.
    import save_metrics

    cw = MagicMock()
    with patch("save_metrics._get_cloudwatch", return_value=cw):
        save_metrics._emit_cloudwatch_metrics(
            {"resumes": 0, "cover_letters": 0, "resume_failures": 0}, 0)
    data = cw.put_metric_data.call_args.kwargs["MetricData"]
    matched = [d for d in data if d["MetricName"] == "JobsMatched"]
    assert matched and matched[0]["Value"] == 0


# --------------------------------------------------------------------------
# A tiny interpreter for the metric-math subset the expression uses.
#
# Semantics follow the CloudWatch "Metric math syntax and functions" page:
#   * HOUR/DAY "return a new non-sparse time series where each value is based
#     on its timestamp"; DAY is 1=Monday .. 7=Sunday, UTC.
#   * FILL "fills the missing values of a time series".
#   * comparison/logical ops between two series treat a missing value as 0.
#   * IF(cond, scalar, series): if cond is FALSE and the series has no point
#     at that timestamp, the output point "is dropped".
# A series is {timestamp: value}; `rng` is every timestamp in the query range.
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"\s*(==|!=|<=|>=|<|>|\|\||&&|\(|\)|,|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?)")


def _tokens(expr):
    pos, out = 0, []
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        assert m, f"cannot tokenise {expr[pos:]!r}"
        out.append(m.group(1))
        pos = m.end()
    return out


class _Eval:
    def __init__(self, expr, metrics, rng):
        self.toks, self.i, self.metrics, self.rng = _tokens(expr), 0, metrics, rng

    def run(self):
        v = self.or_()
        assert self.i == len(self.toks), f"trailing tokens {self.toks[self.i:]}"
        return v

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, want=None):
        t = self.peek()
        assert want is None or t == want, f"expected {want}, got {t}"
        self.i += 1
        return t

    @staticmethod
    def _pair(a, b, op):
        if isinstance(a, dict) and isinstance(b, dict):
            return {t: float(op(a.get(t, 0), b.get(t, 0))) for t in a.keys() | b.keys()}
        if isinstance(a, dict):
            return {t: float(op(v, b)) for t, v in a.items()}
        if isinstance(b, dict):
            return {t: float(op(a, v)) for t, v in b.items()}
        return float(op(a, b))

    def or_(self):
        v = self.and_()
        while self.peek() in ("OR", "||"):
            self.take()
            v = self._pair(v, self.and_(), lambda x, y: bool(x) or bool(y))
        return v

    def and_(self):
        v = self.cmp()
        while self.peek() in ("AND", "&&"):
            self.take()
            v = self._pair(v, self.cmp(), lambda x, y: bool(x) and bool(y))
        return v

    _OPS = {"==": lambda x, y: x == y, "!=": lambda x, y: x != y, "<": lambda x, y: x < y,
            "<=": lambda x, y: x <= y, ">": lambda x, y: x > y, ">=": lambda x, y: x >= y}

    def cmp(self):
        v = self.primary()
        if self.peek() in self._OPS:
            op = self._OPS[self.take()]
            v = self._pair(v, self.primary(), op)
        return v

    def args(self):
        self.take("(")
        out = [self.or_()]
        while self.peek() == ",":
            self.take()
            out.append(self.or_())
        self.take(")")
        return out

    def primary(self):
        t = self.take()
        if t == "(":
            v = self.or_()
            self.take(")")
            return v
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            return float(t)
        if t == "HOUR":
            (x,) = self.args()
            assert isinstance(x, dict)
            return {ts: float(datetime.fromtimestamp(ts, timezone.utc).hour) for ts in self.rng}
        if t == "DAY":
            (x,) = self.args()
            assert isinstance(x, dict)
            return {ts: float(datetime.fromtimestamp(ts, timezone.utc).isoweekday())
                    for ts in self.rng}
        if t == "FILL":
            x, fill = self.args()
            assert isinstance(x, dict) and not isinstance(fill, dict)
            return {ts: x.get(ts, fill) for ts in self.rng}
        if t == "IF":
            cond, a, b = self.args()
            assert isinstance(cond, dict)
            out = {}
            for ts, c in cond.items():
                branch = a if c != 0 else b
                if not isinstance(branch, dict):
                    out[ts] = branch
                elif ts in branch:
                    out[ts] = branch[ts]
                elif c != 0:
                    out[ts] = 0.0  # documented: TRUE with no point in metric2 -> 0
                # FALSE with no point in metric3 -> dropped
            return out
        assert t in self.metrics, f"unknown id or function {t}"
        return self.metrics[t]


def test_interpreter_matches_the_documented_if_example():
    """Calibrate the instrument against the docs' own worked example:
    metric1 [1,1,0,0,-], scalar2 5, metric3 [0,0,20,-,20] -> [5,5,20,-,-]."""
    rng = [0, 1, 2, 3, 4]
    metrics = {"a": {0: 1, 1: 1, 2: 0, 3: 0}, "c": {0: 0, 1: 0, 2: 20, 4: 20}}
    assert _Eval("IF(a, 5, c)", metrics, rng).run() == {0: 5, 1: 5, 2: 20}


# --------------------------------------------------------------------------
# Simulation: a sliding 24h window, evaluated every 15 minutes for 10 days.
# --------------------------------------------------------------------------

MONDAY = datetime(2026, 10, 5, tzinfo=timezone.utc)  # a Monday
assert MONDAY.isoweekday() == 1


def _runs(publish_after, skip_day=None, zero_day=None):
    """Publish timestamps -> value for two MON-FRI weeks of scheduled runs."""
    hour, minute = _schedule_hour_minute()
    out = {}
    for d in range(-7, 14):
        day = MONDAY + timedelta(days=d)
        if day.isoweekday() >= 6 or day == skip_day:
            continue
        at = day.replace(hour=hour, minute=minute) + publish_after
        out[at] = 0.0 if day == zero_day else 5.0
    return out


def _simulate(name, runs):
    """Return (window_start, state) for every evaluation, state in
    {'OK','ALARM','MISSING'}."""
    props = _resources()[name]["Properties"]
    expr = _returned_expression(props)
    threshold = props["Threshold"]
    results = []
    now = MONDAY - timedelta(days=1)  # Sunday 00:00, covers a weekend each side
    end = MONDAY + timedelta(days=9)
    while now < end:
        start = now - timedelta(seconds=DAY_SECONDS)  # sliding window, stamped at its start
        in_window = [v for at, v in runs.items() if start <= at < now]
        ts = int(start.timestamp())
        m1 = {ts: sum(in_window)} if in_window else {}
        out = _Eval(expr, {"m1": m1}, [ts]).run()
        if ts not in out:
            state = "MISSING"
        else:
            state = "ALARM" if out[ts] < threshold else "OK"
        results.append((start, state))
        now += timedelta(minutes=15)
    return results


WEDNESDAY = MONDAY + timedelta(days=2)


def _holds_wednesday_run(start, publish_after):
    hour, minute = _schedule_hour_minute()
    slot = WEDNESDAY.replace(hour=hour, minute=minute) + publish_after
    return start <= slot < start + timedelta(seconds=DAY_SECONDS)


@pytest.mark.parametrize("name", sorted(WEEKDAY_ALARMS))
@pytest.mark.parametrize("publish_after", [timedelta(minutes=5), timedelta(hours=4, minutes=55)])
@pytest.mark.parametrize("failure", ["no_run", "zero_output"])
def test_only_the_wednesday_with_no_output_breaches(name, publish_after, failure):
    runs = _runs(publish_after,
                 skip_day=WEDNESDAY if failure == "no_run" else None,
                 zero_day=WEDNESDAY if failure == "zero_output" else None)
    results = _simulate(name, runs)

    missing = [s for s, st in results if st == "MISSING"]
    assert not missing, (
        f"the expression produced no datapoint for windows starting {missing[:3]}: "
        f"it must be non-sparse, or an empty weekday rides on TreatMissingData"
    )
    alarms = [s for s, st in results if st == "ALARM"]
    assert alarms, "a Wednesday with no output never alarmed"
    wrong = [s for s in alarms if not _holds_wednesday_run(s, publish_after)]
    assert not wrong, (
        "alarmed on windows that do not cover Wednesday's run: "
        + ", ".join(s.strftime("%a %H:%M") for s in wrong[:5])
    )


@pytest.mark.parametrize("name", sorted(WEEKDAY_ALARMS))
def test_a_normal_week_never_alarms(name):
    results = _simulate(name, _runs(timedelta(minutes=30)))
    bad = [(s.strftime("%a %H:%M"), st) for s, st in results if st != "OK"]
    assert not bad, f"healthy MON-FRI schedule alarmed: {bad[:5]}"
