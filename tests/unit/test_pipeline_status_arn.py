"""Regression tests for the Step Functions execution-ARN reconstruction.

Background: prior to fix/pipeline-status-arn-reconstruction, the
`pipeline_execution_status` endpoint built execution ARNs like:

    arn:aws:states:REGION:ACCT:stateMachine:EXEC_NAME   ← INVALID

instead of the correct shape:

    arn:aws:states:REGION:ACCT:execution:STATE_MACHINE_NAME:EXEC_NAME

That made every poll on /api/pipeline/status/{execution_name} return
404, which user-facing surfaces as "Poll failed: HTTP 404" after Tailor
Resume / Cover Letter on the Add Job page.

These tests pin the helper's contract so the regression can't slip back.
"""
from __future__ import annotations

import pytest

from app import _state_machine_arn_to_execution_arn

REGION = "eu-west-1"
ACCT = "385017713886"


class TestStateMachineArnToExecutionArn:
    def test_daily_pipeline_state_machine(self):
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:DailyPipelineStateMachine-AbCdEf"
        result = _state_machine_arn_to_execution_arn(sm, "exec-1234-uuid")
        assert result == (
            f"arn:aws:states:{REGION}:{ACCT}:execution"
            f":DailyPipelineStateMachine-AbCdEf:exec-1234-uuid"
        )

    def test_single_job_pipeline_state_machine(self):
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:SingleJobPipeline-XyZ"
        result = _state_machine_arn_to_execution_arn(sm, "addjob-2026-04-28")
        assert result == (
            f"arn:aws:states:{REGION}:{ACCT}:execution"
            f":SingleJobPipeline-XyZ:addjob-2026-04-28"
        )

    def test_segment_count_is_eight(self):
        """Valid execution ARNs have exactly 8 colon-separated segments."""
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:Foo"
        result = _state_machine_arn_to_execution_arn(sm, "bar")
        assert len(result.split(":")) == 8

    def test_resource_type_is_execution_not_state_machine(self):
        """The 6th segment must be 'execution', not 'stateMachine' — that's the bug we're fixing."""
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:Foo"
        result = _state_machine_arn_to_execution_arn(sm, "bar")
        assert result.split(":")[5] == "execution"

    def test_execution_name_with_hyphens_preserved(self):
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:Foo"
        exec_name = "single-job-2026-04-28T09-15-30Z-abc123"
        result = _state_machine_arn_to_execution_arn(sm, exec_name)
        assert result.endswith(f":{exec_name}")

    def test_old_broken_reconstruction_does_not_match(self):
        """The pre-fix output (`rsplit(":", 1)[0] + ":" + name`) produced this:"""
        sm = f"arn:aws:states:{REGION}:{ACCT}:stateMachine:Foo"
        broken = sm.rsplit(":", 1)[0] + ":bar"
        fixed = _state_machine_arn_to_execution_arn(sm, "bar")
        assert broken != fixed
        # Sanity: the broken form ends in 'stateMachine', which is the smoking gun
        assert broken.split(":")[-2] == "stateMachine"
        assert fixed.split(":")[-2] != "stateMachine"
