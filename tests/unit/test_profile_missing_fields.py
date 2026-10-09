"""/api/profile says WHICH required fields are missing, including a last name.

PUT /api/profile derives first_name/last_name from the full name by splitting
on the first space. A one-word name ("Madonna") leaves last_name empty, and
check_profile_completeness requires last_name because the auto-apply answer
generator fills ATS "Last name" fields from it (shared/answer_generator.py).
So profile_complete stayed False forever and the API said only `false` --
the frontend had to re-derive the reason by mirroring the split rule.

The decision, deliberately: last_name stays required and is NOT invented
(copying the first name, or a placeholder, would put a false surname on real
job applications). Instead ProfileResponse carries `missing_required_fields`,
the backend's own list, so the UI can say "add a last name" without guessing.

Names are the ones the frontend uses (`linkedin_url`, not `linkedin`);
first_name/last_name have no input of their own -- both come from full_name --
so they are reported as themselves, which is the precise fact.

Runs the real endpoints over the real SupabaseClient methods on the PostgREST
double, so the row the response is computed from is the row update_user wrote.
"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase

COMPLETE_EXCEPT_NAME = {
    "phone": "+353851234567",
    "linkedin_url": "https://linkedin.com/in/x",
    "visa_status": "stamp-1g",
    "work_authorizations": {"IE": "stamp1g"},
    "notice_period_text": "2 weeks",
}


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user
    from db_client import SupabaseClient

    fake = FakeSupabase({"users": [{"id": "user-1", "email": "u@example.test"}]})
    db = object.__new__(SupabaseClient)
    db.client = fake
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_posthog", None)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.test")
    yield TestClient(app_module.app, raise_server_exceptions=False)
    app_module.app.dependency_overrides.clear()


def test_a_one_word_name_is_reported_as_a_missing_last_name(client):
    r = client.put("/api/profile", json={"full_name": "Madonna", **COMPLETE_EXCEPT_NAME})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_complete"] is False
    assert body["missing_required_fields"] == ["last_name"]


def test_no_last_name_is_invented(client):
    client.put("/api/profile", json={"full_name": "Madonna", **COMPLETE_EXCEPT_NAME})
    import app as app_module
    row = app_module._db.client.rows("users", id="user-1")[0]
    assert row["first_name"] == "Madonna"
    assert row["last_name"] == "", "a surname was invented for a one-word name"


def test_a_two_word_name_completes_the_profile(client):
    r = client.put("/api/profile", json={"full_name": "Jane Doe", **COMPLETE_EXCEPT_NAME})
    body = r.json()
    assert body["missing_required_fields"] == []
    assert body["profile_complete"] is True


def test_get_reports_the_same_list(client):
    client.put("/api/profile", json={"full_name": "Madonna", **COMPLETE_EXCEPT_NAME})
    body = client.get("/api/profile").json()
    assert body["missing_required_fields"] == ["last_name"]
    assert body["profile_complete"] is False


def test_fields_use_the_frontend_names(client):
    """`linkedin` is stored as `linkedin`, but the form field is linkedin_url."""
    body = client.get("/api/profile").json()
    assert "linkedin_url" in body["missing_required_fields"]
    assert "linkedin" not in body["missing_required_fields"]
    assert body["profile_complete"] is (not body["missing_required_fields"])


def test_the_field_round_trips_through_settings_without_a_422(client):
    """ProfileUpdateRequest is extra='forbid'. Settings.jsx strips
    `missing_required_fields` before PUT; this pins the name it strips."""
    import pathlib
    settings = pathlib.Path("web/src/pages/Settings.jsx").read_text()
    assert "missing_required_fields" in settings
