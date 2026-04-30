"""Regression test for `SingleJobRunRequest` accepting `apply_url`.

Background: A1 from the 2026-04-28 backend audit. The frontend Add Job
form sends `apply_url` to /api/pipeline/run-single, but
`SingleJobRunRequest` did not declare the field, so Pydantic silently
dropped it. The dropped value flowed into `jobs_raw.apply_url=""`,
which broke the auto-apply pipeline (apply eligibility gate checks
`apply_url`).

Same Pydantic field-strip-on-undeclared bug class as the original
location-plumbing bug (PR #18), just on a different field.
"""
from __future__ import annotations


def test_single_job_run_request_accepts_apply_url():
    from app import SingleJobRunRequest
    req = SingleJobRunRequest(
        job_description="long enough description for the validator " * 5,
        company="Acme",
        apply_url="https://example.com/jobs/123/apply",
    )
    assert req.apply_url == "https://example.com/jobs/123/apply"


def test_single_job_run_request_apply_url_defaults_to_empty():
    from app import SingleJobRunRequest
    req = SingleJobRunRequest(
        job_description="long enough description for the validator " * 5,
    )
    assert req.apply_url == ""


def test_single_job_run_request_apply_url_is_str_typed():
    from app import SingleJobRunRequest
    fields = SingleJobRunRequest.model_fields
    assert "apply_url" in fields, "regression: apply_url field disappeared"
    # Pydantic v2: annotation accessible via .annotation
    annot = fields["apply_url"].annotation
    assert annot is str, f"apply_url should be str, got {annot}"
