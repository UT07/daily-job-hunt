"""Regression test: pipeline_execution_status must NOT swallow non-NotFound
errors as 404.

Background — 2026-04-30 → 2026-05-01 prod outage:

SAM's `StepFunctionsExecutionPolicy` only grants `states:StartExecution`,
not `states:DescribeExecution`. The `JobHuntApi` Lambda used that managed
policy alone, so every poll of /api/pipeline/status/{name} returned 404
because describe_execution() raised AccessDeniedException, which the
endpoint's broad `except Exception` caught and converted into a 404
"Execution not found" response.

The frontend's pollPipeline saw `!res.ok` on the very first poll and
threw "Poll failed: HTTP 404" — surfacing to the user as
"Tailor Resume doesn't work" while the backend was actually succeeding.

This test pins the contract: a non-ExecutionDoesNotExist error must
bubble up as 502, NOT silently 404. That way any future IAM regression
fails loudly with a banner the user can act on, rather than silently
breaking the polling flow.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app_module():
    import app
    return app


def _make_access_denied_error():
    """Mimic a botocore ClientError with AccessDeniedException code."""
    from botocore.exceptions import ClientError
    return ClientError(
        error_response={
            "Error": {
                "Code": "AccessDeniedException",
                "Message": (
                    "User: arn:aws:sts::123:assumed-role/Foo is not authorized "
                    "to perform: states:DescribeExecution on resource: "
                    "arn:aws:states:eu-west-1:123:execution:Foo:bar"
                ),
            }
        },
        operation_name="DescribeExecution",
    )


def _make_execution_does_not_exist_error(sfn_client):
    """Mimic the boto3 modelled exception class."""
    return sfn_client.exceptions.ExecutionDoesNotExist(
        error_response={
            "Error": {
                "Code": "ExecutionDoesNotExist",
                "Message": "Execution Does Not Exist",
            }
        },
        operation_name="DescribeExecution",
    )


def test_access_denied_surfaces_as_502_not_404(app_module, monkeypatch):
    """If describe_execution raises AccessDenied (e.g. missing IAM permission),
    the endpoint must return 502 — not 404. A 404 looks like 'no such
    execution' to the frontend and silently breaks the polling loop."""
    monkeypatch.setenv("DAILY_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Daily")
    monkeypatch.setenv("SINGLE_JOB_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Single")

    sfn_mock = MagicMock()
    # Set up the modelled exception classes that boto3 normally generates
    sfn_mock.exceptions.ExecutionDoesNotExist = type(
        "ExecutionDoesNotExist", (Exception,), {}
    )
    # Always raise AccessDenied — both candidates fail with permission error
    sfn_mock.describe_execution.side_effect = _make_access_denied_error()

    fake_user = MagicMock(id="user-123")

    with patch.object(app_module, "_get_sfn", return_value=sfn_mock):
        with pytest.raises(HTTPException) as exc:
            app_module.pipeline_execution_status("any-exec-name", user=fake_user)

    assert exc.value.status_code == 502, (
        f"AccessDenied must surface as 502, got {exc.value.status_code}. "
        "If this fails, the silent-IAM-failure regression is back: the "
        "frontend pollPipeline will see 404 and bail out, surfacing as "
        "'Tailor Resume doesn't work' to users."
    )
    assert "failed" in str(exc.value.detail).lower() or "permission" in str(exc.value.detail).lower() \
        or "AccessDenied" in str(exc.value.detail)


def test_execution_does_not_exist_in_either_state_machine_returns_404(app_module, monkeypatch):
    """If both candidates legitimately don't have the execution, return 404."""
    monkeypatch.setenv("DAILY_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Daily")
    monkeypatch.setenv("SINGLE_JOB_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Single")

    sfn_mock = MagicMock()
    # Define the exception class first so the side_effect can reference it
    class _ExecDoesNotExist(Exception):
        pass
    sfn_mock.exceptions.ExecutionDoesNotExist = _ExecDoesNotExist
    sfn_mock.describe_execution.side_effect = _ExecDoesNotExist("nope")

    fake_user = MagicMock(id="user-123")

    with patch.object(app_module, "_get_sfn", return_value=sfn_mock):
        with pytest.raises(HTTPException) as exc:
            app_module.pipeline_execution_status("ghost-exec", user=fake_user)

    assert exc.value.status_code == 404


def test_first_candidate_misses_second_succeeds(app_module, monkeypatch):
    """If the first state machine doesn't have it but the second does, return 200 with status."""
    from datetime import datetime
    monkeypatch.setenv("DAILY_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Daily")
    monkeypatch.setenv("SINGLE_JOB_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:Single")

    sfn_mock = MagicMock()
    class _ExecDoesNotExist(Exception):
        pass
    sfn_mock.exceptions.ExecutionDoesNotExist = _ExecDoesNotExist
    sfn_mock.describe_execution.side_effect = [
        _ExecDoesNotExist("not in daily"),
        {
            "name": "exec-abc",
            "status": "SUCCEEDED",
            "startDate": datetime(2026, 5, 1, 0, 0, 0),
            "stopDate": datetime(2026, 5, 1, 0, 5, 0),
            "output": '{"job_id": "abc"}',
        },
    ]

    fake_user = MagicMock(id="user-123")

    with patch.object(app_module, "_get_sfn", return_value=sfn_mock):
        result = app_module.pipeline_execution_status("exec-abc", user=fake_user)

    assert result["status"] == "SUCCEEDED"
    assert result["output"] == {"job_id": "abc"}
