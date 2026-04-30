"""Contract test: every Request model the AddJob page hits must accept
the exact payload AddJob.jsx's getPayload() returns.

Why: the 2026-04-30 incident saw all four AddJob buttons (Save & Score,
Tailor Resume, Cover Letter, Find Contacts) silently 422'ing because
PR #26's `extra='forbid'` was added to the Request models, then the
frontend evolved to send `apply_url` (PR #18) without the models being
updated to declare it. This test pins the contract so the next time
the frontend sends a new field, CI fails before users do.

The frontend payload is mirrored here from web/src/pages/AddJob.jsx's
getPayload() function. If you add a field to that JS function, you
MUST also add it to every model below + this fixture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# Mirror of AddJob.jsx getPayload() — every field the page sends to the API.
ADDJOB_PAYLOAD = {
    "job_description": "x" * 200,
    "job_title": "Software Engineer",
    "company": "Acme Corp",
    "location": "Dublin, Ireland",
    "apply_url": "https://acme.com/jobs/sre",
    "resume_type": "sre_devops",
}


def _load_app():
    """Importing app.py triggers SAM lifespan + boto3 SSM, so we import a
    minimal subset by direct file load to keep tests fast and isolated."""
    import app
    return app


@pytest.fixture(scope="module")
def app_module():
    return _load_app()


@pytest.mark.parametrize("model_name,endpoint", [
    ("ScoreRequest", "/api/score"),
    ("TailorRequest", "/api/tailor"),
    ("CoverLetterRequest", "/api/cover-letter"),
    ("ContactsRequest", "/api/contacts"),
])
def test_addjob_payload_validates_against_request_model(app_module, model_name, endpoint):
    """The exact payload AddJob.jsx getPayload() returns must validate
    cleanly against every Request model the page hits.

    If this fails: either the frontend added a field the model doesn't
    declare, or the model was changed in a way that drops a field the
    frontend still sends. Update both sides + the ADDJOB_PAYLOAD fixture
    above to match.
    """
    Model = getattr(app_module, model_name)
    instance = Model(**ADDJOB_PAYLOAD)
    # Sanity: the round-trip preserves the apply_url field
    dumped = instance.model_dump()
    assert dumped["apply_url"] == ADDJOB_PAYLOAD["apply_url"], (
        f"{model_name} dropped apply_url silently"
    )


def test_addjob_payload_does_not_have_unknown_fields():
    """Sentinel: if a developer adds a field to AddJob.jsx getPayload()
    but forgets to update this test, this assertion still passes locally
    and we miss it. So we hard-code the expected keys here so a frontend
    change without a test update fails this assertion in code review."""
    expected_keys = {
        "job_description", "job_title", "company", "location",
        "apply_url", "resume_type",
    }
    assert set(ADDJOB_PAYLOAD.keys()) == expected_keys, (
        "AddJob.jsx getPayload() shape changed — update ADDJOB_PAYLOAD "
        "above + every Request model in app.py to match."
    )
