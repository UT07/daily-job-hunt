"""Uploading a résumé may FILL an empty profile, never overwrite one.

Live run 2026-10-09: the user typed a name, phone and location in Settings,
then re-uploaded a résumé. POST /api/resumes/upload called update_user with
the name, phone and location PARSED from the document, and what the user had
typed was gone. The same block wrote `candidate_context` from the skills
section, replacing whatever context the user had written.

Onboarding relies on the fill: a new user's profile is empty and the upload
is what populates it. So both halves are tested, against the real
SupabaseClient over the filtering PostgREST double.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db_client
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase

USER = "ee449fe1-7c97-4ba2-96ea-54bf2a1ce20a"
TEX = "\\documentclass{article}\n\\begin{document}\n\\section{Experience} Built things.\n\\end{document}\n"
PARSED = {
    "name": "Parsed From Resume",
    "phone": "+44 7700 900000",
    "location": "London, UK",
    "skills": "Python, Kubernetes",
}


def _upload(user_row):
    fake = FakeSupabase({"users": [user_row], "user_resumes": []})
    with patch.object(db_client, "create_client", return_value=fake):
        db = db_client.SupabaseClient("https://example.supabase.co", "service-key")

    import resume_parser
    bullets = types.ModuleType("retrieval.bullets")
    bullets.index_bullets = lambda *a, **k: 0
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=USER, email="e@x.y")
    try:
        with patch.object(app_module, "_db", db), \
             patch.object(app_module, "_posthog", None), \
             patch.object(resume_parser, "parse_resume_sections", lambda text, ai_client=None: dict(PARSED)), \
             patch.dict(sys.modules, {"retrieval.bullets": bullets}), \
             patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
            r = TestClient(app_module.app).post(
                "/api/resumes/upload", files={"file": ("cv.tex", TEX.encode(), "text/plain")})
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert fake.rows("user_resumes", user_id=USER), "upload did not reach the database"
    (row,) = fake.rows("users", id=USER)
    return row


def test_a_reupload_does_not_overwrite_what_the_user_typed():
    typed = {
        "id": USER, "email": "e@x.y",
        "name": "Typed Name", "phone": "+353 1 234 5678", "location": "Dublin, Ireland",
        "candidate_context": "I want platform roles.",
    }
    row = _upload(dict(typed))
    for field in ("name", "phone", "location", "candidate_context"):
        assert row[field] == typed[field], field


def test_an_empty_profile_is_filled_from_the_resume():
    """Onboarding: nothing typed yet, so the upload populates the profile."""
    row = _upload({"id": USER, "email": "e@x.y", "name": None, "phone": "", "location": "   "})
    assert row["name"] == "Parsed From Resume"
    assert row["phone"] == "+44 7700 900000"
    assert row["location"] == "London, UK"
    assert row["candidate_context"] == "Python, Kubernetes"


def test_only_the_empty_fields_are_filled():
    row = _upload({"id": USER, "email": "e@x.y", "name": "Typed Name", "phone": None, "location": ""})
    assert row["name"] == "Typed Name"
    assert row["phone"] == "+44 7700 900000"
    assert row["location"] == "London, UK"


@pytest.mark.parametrize("field", ["name", "phone", "location"])
def test_the_double_really_writes_profile_fields(field):
    """Soundness: with the field empty the write lands, so a pass above is
    the code declining to write, not the double ignoring the update."""
    row = _upload({"id": USER, "email": "e@x.y", field: None})
    assert row[field] == PARSED[field]
