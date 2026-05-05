"""Contract test: ProfileResponse must expose `profile_complete: bool` derived
from shared.profile_completeness.check_profile_completeness().

Why: Phase 1 of Smart Apply (spec §4) needs an authoritative completeness
signal on /api/profile. Without this, frontend AppLayout has its own local
heuristic (full_name && phone && location) that drifts from the backend's
9-required-fields check.
"""
from __future__ import annotations
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    app_module = importlib.import_module("app")
    return TestClient(app_module.app)


def test_profile_response_model_has_profile_complete_field():
    """The Pydantic model must declare the field — not just inject it ad hoc."""
    from app import ProfileResponse

    fields = ProfileResponse.model_fields
    assert "profile_complete" in fields, (
        "ProfileResponse must declare profile_complete: bool. "
        "Phase 1 frontend reads this — see docs/superpowers/specs/2026-05-01-smart-apply-phase1-design.md §2 backend dependency."
    )
    annotation = fields["profile_complete"].annotation
    assert annotation is bool, f"profile_complete must be bool, got {annotation}"
