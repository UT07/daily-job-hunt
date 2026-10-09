"""An account with a pending GDPR deletion cannot keep using the API.

DELETE /api/gdpr/delete only stamps users.gdpr_deletion_requested_at; the hard
delete is scripts/data_retention.py after a 30-day grace, and nothing runs it.
Sign-in is Supabase's and still succeeds, so before this the account went on
working exactly as before: the "deletion" changed nothing the user could see,
and every Save & Score or Add Job during the grace period created more data
the deletion was supposed to remove.

app.get_current_user now wraps the JWT check and refuses a user whose
deletion is requested with a 403 that says why. Two routes stay open on
purpose, on the raw JWT dependency: GET /api/gdpr/export (the right of access
lasts until the data is gone) and DELETE /api/gdpr/delete (repeating the
request must not lock the user out of the page that made it).

Over the real SupabaseClient on the PostgREST double, so the gate reads the
same `users` row update_user/request_deletion wrote.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def world(monkeypatch):
    import app as app_module
    import auth
    from db_client import SupabaseClient

    fake = FakeSupabase({"users": [
        {"id": "leaving", "email": "l@example.test",
         "gdpr_deletion_requested_at": "2026-10-01T09:00:00"},
        {"id": "staying", "email": "s@example.test", "gdpr_deletion_requested_at": None},
    ], "jobs": [], "pipeline_tasks": []})
    db = object.__new__(SupabaseClient)
    db.client = fake
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_posthog", None)
    who = {"id": "leaving"}
    # Override the JWT check only -- the layer below the gate -- exactly as
    # every other test in the suite does.
    app_module.app.dependency_overrides[auth.get_current_user] = lambda: auth.AuthUser(
        id=who["id"], email=f"{who['id']}@example.test")
    yield TestClient(app_module.app, raise_server_exceptions=False), fake, who
    app_module.app.dependency_overrides.clear()


def test_a_user_with_a_pending_deletion_gets_403_with_a_reason(world):
    client, _, _ = world
    r = client.get("/api/profile")
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert "deletion" in detail.lower()
    assert "2026-10-01" in detail


def test_it_blocks_writes_too(world):
    client, fake, _ = world
    r = client.post("/api/score", json={
        "job_description": "We are hiring an SRE to own Kubernetes and on-call. " * 3,
        "job_title": "SRE", "company": "Acme"})
    assert r.status_code == 403
    assert fake.rows("jobs") == []


def test_an_ordinary_user_is_unaffected(world):
    client, _, who = world
    who["id"] = "staying"
    assert client.get("/api/profile").status_code == 200


def test_export_stays_open_during_the_grace_period(world):
    client, _, _ = world
    with patch("gdpr.export_user_data", return_value=b"PK"):
        r = client.get("/api/gdpr/export")
    assert r.status_code == 200, r.text


def test_repeating_the_delete_request_is_not_refused(world):
    client, _, _ = world
    assert client.delete("/api/gdpr/delete").status_code == 200


def test_a_mock_db_does_not_read_as_a_deletion(world, monkeypatch):
    """A MagicMock row answers every .get() with a truthy mock. Only a real
    timestamp string may block, or half the suite would 403 (CLAUDE.md #6)."""
    import app as app_module
    client, _, _ = world
    monkeypatch.setattr(app_module, "_db", MagicMock())
    r = client.get("/api/quality-stats")
    assert r.status_code != 403


def test_only_the_two_gdpr_routes_bypass_the_gate():
    """Structural: any NEW route on the raw JWT dependency is a hole in the
    gate, so the bypass list is pinned."""
    import app as app_module
    import auth

    bypass = set()
    for route in app_module.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in route.dependant.dependencies:
            if dep.call is auth.get_current_user:
                bypass |= {(m, route.path) for m in route.methods}
    assert bypass == {("GET", "/api/gdpr/export"), ("DELETE", "/api/gdpr/delete")}
    assert app_module.get_current_user is not auth.get_current_user
