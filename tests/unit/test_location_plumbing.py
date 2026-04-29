"""Unit tests for job-location plumbing through API request models.

Regression tests for the bug where the Add Job form sent `location` but the
Pydantic request models did not declare it, so FastAPI silently dropped it
and the AI scoring prompt rendered "Location: " (empty) — causing the model
to emit "no location specified" even when the user typed "Dublin".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Set required env vars BEFORE importing app
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "super-secret-jwt-key-for-testing-only-32chars!")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")
os.environ.setdefault("SINGLE_JOB_PIPELINE_ARN", "arn:aws:states:eu-west-1:123:stateMachine:test-single")


# ---------------------------------------------------------------------------
# Schema tests — pin that each request model declares `location`.
# Without this, FastAPI silently drops the field from incoming JSON.
# ---------------------------------------------------------------------------


class TestRequestModelSchemas:
    def test_score_request_has_location(self):
        from app import ScoreRequest
        fields = ScoreRequest.model_fields
        assert "location" in fields, (
            "ScoreRequest must declare `location`; otherwise FastAPI drops it "
            "from the JSON payload and the AI prompt sees an empty Location."
        )
        assert fields["location"].default == ""

    def test_tailor_request_has_location(self):
        from app import TailorRequest
        assert "location" in TailorRequest.model_fields

    def test_cover_letter_request_has_location(self):
        from app import CoverLetterRequest
        assert "location" in CoverLetterRequest.model_fields

    def test_contacts_request_has_location(self):
        from app import ContactsRequest
        assert "location" in ContactsRequest.model_fields

    def test_single_job_run_request_has_location(self):
        from app import SingleJobRunRequest
        assert "location" in SingleJobRunRequest.model_fields

    def test_score_request_accepts_location_in_payload(self):
        """Concrete proof: parsing JSON with `location` does NOT drop it."""
        from app import ScoreRequest
        req = ScoreRequest(
            job_description="x" * 50,
            job_title="SRE",
            company="Acme",
            location="Dublin, Ireland",
            resume_type="sre_devops",
        )
        assert req.location == "Dublin, Ireland"

    def test_score_request_location_defaults_to_empty(self):
        """When the client omits location, default is empty string (not None)."""
        from app import ScoreRequest
        req = ScoreRequest(job_description="x" * 50)
        assert req.location == ""


# ---------------------------------------------------------------------------
# _Job constructor tests — pin that location flows through.
# ---------------------------------------------------------------------------


class TestJobConstructor:
    def test_job_accepts_location(self):
        from app import _Job
        job = _Job(
            title="SRE",
            company="Acme",
            description="Build pipelines",
            location="Dublin, Ireland",
        )
        assert job.location == "Dublin, Ireland"

    def test_job_location_defaults_to_empty(self):
        from app import _Job
        job = _Job(title="SRE", company="Acme", description="d")
        assert job.location == ""

    def test_job_location_none_coerced_to_empty(self):
        from app import _Job
        job = _Job(title="SRE", company="Acme", description="d", location=None)  # type: ignore[arg-type]
        assert job.location == ""


# ---------------------------------------------------------------------------
# Step Functions input shape — pin that /api/pipeline/run-single forwards
# location into the SFN execution input dict.
# ---------------------------------------------------------------------------


def _make_hs256_jwt(payload: dict) -> str:
    from jose import jwt as jose_jwt
    secret = os.environ["SUPABASE_JWT_SECRET"]
    default_payload = {
        "sub": "user-123",
        "email": "test@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": 9999999999,
        "iat": 1700000000,
    }
    default_payload.update(payload)
    return jose_jwt.encode(default_payload, secret, algorithm="HS256")


class TestSingleJobPipelineSFNInput:
    def test_run_single_job_includes_location_in_sfn_input(self):
        """POST /api/pipeline/run-single must forward `location` into the
        Step Functions execution input — otherwise the score lambda never
        sees what the user typed in the form."""
        from datetime import datetime, timezone
        from fastapi.testclient import TestClient

        import app as app_module

        # Capture the SFN input dict
        captured: dict = {}

        sfn_mock = MagicMock()

        def _start_execution(stateMachineArn, input):  # noqa: N803 — boto3 kwarg
            captured["arn"] = stateMachineArn
            captured["input"] = json.loads(input)
            return {
                "executionArn": (
                    "arn:aws:states:eu-west-1:123:execution:test-single:exec-1"
                ),
                "startDate": datetime.now(timezone.utc),
            }

        sfn_mock.start_execution.side_effect = _start_execution

        with patch.object(app_module, "_get_sfn", return_value=sfn_mock):
            client = TestClient(app_module.app, raise_server_exceptions=False)
            token = _make_hs256_jwt({})
            resp = client.post(
                "/api/pipeline/run-single",
                json={
                    "job_description": "We are hiring an SRE. " * 10,
                    "job_title": "Senior SRE",
                    "company": "Acme",
                    "location": "Dublin, Ireland",
                    "resume_type": "sre_devops",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 202, resp.text
        assert captured["input"]["location"] == "Dublin, Ireland", (
            "SFN input dict must carry `location` so score_batch / matcher "
            "can render the AI prompt with the correct job location."
        )
        # Sanity: the rest of the keys are still there
        assert captured["input"]["job_title"] == "Senior SRE"
        assert captured["input"]["company"] == "Acme"
        assert captured["input"]["resume_type"] == "sre_devops"
        assert captured["input"]["skip_scoring"] is False


# ---------------------------------------------------------------------------
# Matcher prompt template — pin that the {location} placeholder is present
# so a future refactor can't silently drop it from the AI prompt.
# ---------------------------------------------------------------------------


class TestMatcherPromptIncludesLocation:
    def test_format_job_for_prompt_includes_location(self):
        from matcher import _format_job_for_prompt
        from scrapers.base import Job

        job = Job(
            title="SRE",
            company="Acme",
            location="Dublin, Ireland",
            description="Build pipelines.",
            apply_url="",
            source="web",
        )
        rendered = _format_job_for_prompt(job, 0)
        assert "Location: Dublin, Ireland" in rendered, (
            "matcher._format_job_for_prompt must emit 'Location: <value>' so "
            "the AI sees the job location in its scoring prompt."
        )
