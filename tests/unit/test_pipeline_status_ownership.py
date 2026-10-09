"""Pipeline status must only show a caller their own executions.

GET /api/pipeline/status/{execution_name} described any execution by name and
returned its output -- which carries user_id, the JD and S3 keys -- to any
authenticated user. GET /api/pipeline/status fell back to the daily pipeline's
latest execution for every user. Ownership is the execution INPUT's user_id:
every start_execution in app.py and the EventBridge rule set it.

A not-yours execution is a 404 with the same message as a missing one, so the
endpoint does not confirm that someone else's execution exists.

A FAILED / TIMED_OUT / ABORTED execution returns `error` and `cause`, which the
frontend reads to say why. They are sanitised: Step Functions' cause is often
a Lambda error JSON with a stackTrace, ARNs, S3 keys and presigned URLs.
"""
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app as app_module
from auth import AuthUser

ALICE = AuthUser(id="alice-0000", email="a@x")
SUCCESS_KEYS = {"name", "status", "startDate", "stopDate", "output"}


class _Missing(Exception):
    pass


def _sfn(desc):
    sfn = MagicMock()
    sfn.exceptions.ExecutionDoesNotExist = _Missing
    sfn.describe_execution.return_value = desc
    return sfn


def _desc(owner, status="SUCCEEDED", **kw):
    d = {
        "name": "exec-1", "status": status,
        "startDate": datetime(2026, 10, 8, 9, 0), "stopDate": datetime(2026, 10, 8, 9, 5),
        "input": json.dumps({"user_id": owner, "job_hash": "h"}) if owner is not None else "{}",
        "output": json.dumps({"user_id": owner, "description": "secret JD",
                              "resume_s3_key": f"users/{owner}/resumes/h.pdf"}),
    }
    d.update(kw)
    return d


@pytest.fixture
def poll(monkeypatch):
    monkeypatch.setenv("DAILY_PIPELINE_ARN", "arn:aws:states:eu-west-1:1:stateMachine:Daily")
    monkeypatch.delenv("SINGLE_JOB_PIPELINE_ARN", raising=False)

    def run(desc):
        monkeypatch.setattr(app_module, "_get_sfn", lambda: _sfn(desc))
        return app_module.pipeline_execution_status("exec-1", user=ALICE)
    return run


def test_own_execution_is_returned_with_unchanged_success_keys(poll):
    out = poll(_desc("alice-0000"))
    assert set(out) == SUCCESS_KEYS
    assert out["output"]["user_id"] == "alice-0000"


def test_another_users_execution_is_404(poll):
    with pytest.raises(HTTPException) as e:
        poll(_desc("bob-1111"))
    assert e.value.status_code == 404
    assert "secret JD" not in str(e.value.detail)
    assert e.value.detail == "Execution not found: exec-1"


def test_an_execution_with_no_owner_is_404(poll):
    with pytest.raises(HTTPException) as e:
        poll(_desc(None))
    assert e.value.status_code == 404


def test_unparseable_input_is_404(poll):
    with pytest.raises(HTTPException) as e:
        poll(_desc("alice-0000", input="not json"))
    assert e.value.status_code == 404


LAMBDA_CAUSE = json.dumps({
    "errorMessage": (
        "Job 3f2a9c1e-1b2c-4d5e-8f90-abcdef123456 not found in jobs_raw; key "
        "users/bob-1111/resumes/abc_tailored.tex at "
        "https://bucket.s3.amazonaws.com/x.pdf?X-Amz-Signature=deadbeef "
        "via arn:aws:lambda:eu-west-1:385017713886:function:Tailor token "
        "fake_token_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "errorType": "ValueError",
    "stackTrace": ['  File "/var/task/tailor_resume.py", line 1172, in handler\n'],
})


def test_a_failed_execution_explains_itself_sanitised(poll):
    out = poll(_desc("alice-0000", status="FAILED", error="States.TaskFailed",
                     cause=LAMBDA_CAUSE, output=None))
    assert out["error"] == "States.TaskFailed"
    cause = out["cause"]
    assert cause.startswith("Job ")
    assert "not found in jobs_raw" in cause
    for leak in ("stackTrace", "/var/task", "users/bob", "X-Amz", "arn:aws",
                 "3f2a9c1e-1b2c", "fake_token_", "385017713886", "https://"):
        assert leak not in cause, f"{leak!r} leaked into cause: {cause}"
    assert len(cause) <= 300


def test_a_python_traceback_cause_is_cut_at_the_traceback(poll):
    out = poll(_desc("alice-0000", status="FAILED", error="JobFailed",
                     cause="Tailoring failed\nTraceback (most recent call last):\n  File x", output=None))
    assert out["cause"] == "Tailoring failed"


def test_a_traceback_on_the_same_line_is_cut(poll):
    out = poll(_desc("alice-0000", status="FAILED", error="JobFailed",
                     cause="Tailoring failed Traceback (most recent call last): File x", output=None))
    assert out["cause"] == "Tailoring failed"


def test_short_identifiers_are_redacted_too(poll):
    """Each below is under the 32-char long-token rule, so only its own rule
    catches it."""
    out = poll(_desc("alice-0000", status="FAILED", error="JobFailed", output=None,
                     cause="no key users/u1/r/a.tex in account 385017713886 for bob@x.io"))
    assert "users/u1" not in out["cause"]
    assert "bob@x.io" not in out["cause"]
    assert "385017713886" not in out["cause"]


def test_a_long_cause_is_capped(poll):
    out = poll(_desc("alice-0000", status="FAILED", error="JobFailed", output=None,
                     cause="word " * 200))
    assert len(out["cause"]) == 300


def test_a_timeout_has_an_error_and_cause_even_without_them(poll):
    out = poll(_desc("alice-0000", status="TIMED_OUT", output=None))
    assert out["error"] and out["cause"]


def test_a_hostile_error_name_is_not_echoed(poll):
    out = poll(_desc("alice-0000", status="FAILED",
                     error="https://evil.example/users/bob-1111/x", cause="x", output=None))
    assert "evil" not in out["error"] and "bob" not in out["error"]


# ── GET /api/pipeline/status: the daily-pipeline fallback ────────────────────

def _latest(monkeypatch, executions, descs):
    db = MagicMock()
    db.get_runs.return_value = []
    monkeypatch.setattr(app_module, "_db", db)
    sfn = MagicMock()
    sfn.list_executions.return_value = {"executions": executions}
    sfn.describe_execution.side_effect = lambda executionArn: descs[executionArn]
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: sfn)
    return app_module.pipeline_status(user=ALICE)["latest_run"]


def _ex(arn, status, hour):
    return {"executionArn": arn, "status": status,
            "startDate": datetime(2026, 10, 8, hour), "stopDate": datetime(2026, 10, 8, hour, 5)}


def test_the_fallback_skips_other_users_executions(monkeypatch):
    latest = _latest(monkeypatch,
                     [_ex("bob-new", "SUCCEEDED", 12), _ex("alice-old", "SUCCEEDED", 9)],
                     {"bob-new": {"input": json.dumps({"user_id": "bob-1111"}),
                                  "output": json.dumps({"score_result": {"matched_count": 99}})},
                      "alice-old": {"input": json.dumps({"user_id": "alice-0000"}),
                                    "output": json.dumps({"score_result": {"matched_count": 3}})}})
    assert latest["started_at"].startswith("2026-10-08T09")
    assert latest["jobs_matched"] == 3


def test_the_fallback_shows_nothing_when_no_execution_is_yours(monkeypatch):
    latest = _latest(monkeypatch, [_ex("bob-new", "SUCCEEDED", 12)],
                     {"bob-new": {"input": json.dumps({"user_id": "bob-1111"}), "output": "{}"}})
    assert latest is None
