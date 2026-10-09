"""PUT /api/search-config must not report a save it did not make.

Live run 2026-10-09: Settings -> Job Sources sent `PUT /api/search-config`
with `enabled_sources=[...]`, got 200, and showed "Job sources saved.". The
next GET had no `enabled_sources` key at all. Production has no such column
(read-only select, same day: 42703 "column user_search_configs.enabled_sources
does not exist"), and `upsert_search_config` drops a missing column and
retries -- so the endpoint answered 200 whether it stored the field or not
(CLAUDE.md #2).

The schema here is enforced the way PostgREST enforces it on a write: an
unknown column in the payload is PGRST204, wording included.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db_client
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase, Query

USER = "ee449fe1-7c97-4ba2-96ea-54bf2a1ce20a"
PROD_COLUMNS = {
    "id", "user_id", "queries", "locations", "geo_regions", "experience_levels",
    "days_back", "max_jobs_per_run", "min_match_score", "created_at", "updated_at",
}


class _SchemaQuery(Query):
    def execute(self):
        if self._table == "user_search_configs" and self._op == "upsert":
            unknown = sorted(set(self._payload) - self._db.columns)
            if unknown:
                raise Exception({
                    "code": "PGRST204", "details": None, "hint": None,
                    "message": f"Could not find the '{unknown[0]}' column of "
                               f"'user_search_configs' in the schema cache",
                })
        res = super().execute()
        if self._table == "user_search_configs" and self._op == "upsert":
            # PostgREST returns the whole row: every column, NULL where unset.
            res = SimpleNamespace(
                data=[{c: r.get(c) for c in self._db.columns} for r in res.data], count=None)
        return res


class _SchemaDB(FakeSupabase):
    def __init__(self, tables, columns):
        super().__init__(tables)
        self.columns = set(columns)

    def table(self, name):
        return _SchemaQuery(self, name)


def _api(columns):
    fake = _SchemaDB({
        "users": [{"id": USER, "email": "e@x.y"}],
        "user_search_configs": [{"id": "c1", "user_id": USER, "queries": ["SRE"]}],
    }, columns)
    with patch.object(db_client, "create_client", return_value=fake):
        db = db_client.SupabaseClient("https://example.supabase.co", "service-key")
    return fake, db


@pytest.fixture
def client_for(monkeypatch):
    def _make(columns):
        fake, db = _api(columns)
        monkeypatch.setattr(app_module, "_db", db)
        app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=USER, email="e@x.y")
        return fake, TestClient(app_module.app)

    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield _make
    app_module.app.dependency_overrides.clear()


def test_a_save_that_stored_nothing_is_an_error_not_a_200(client_for):
    fake, c = client_for(PROD_COLUMNS)
    r = c.put("/api/search-config", json={"enabled_sources": ["linkedin", "indeed"]})
    assert r.status_code == 409, r.text
    assert "enabled_sources" in r.json()["detail"]
    assert "enabled_sources" not in c.get("/api/search-config").json()


def test_a_partial_save_names_what_it_dropped(client_for):
    fake, c = client_for(PROD_COLUMNS)
    r = c.put("/api/search-config", json={"queries": ["DevOps"], "enabled_sources": ["linkedin"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["not_saved"] == ["enabled_sources"]
    assert "enabled_sources" in body["warning"]
    assert fake.rows("user_search_configs", user_id=USER)[0]["queries"] == ["DevOps"]


def test_once_the_column_exists_the_save_is_clean_and_reads_back(client_for):
    fake, c = client_for(PROD_COLUMNS | {"enabled_sources"})
    r = c.put("/api/search-config", json={"enabled_sources": ["linkedin"]})
    assert r.status_code == 200, r.text
    assert "not_saved" not in r.json()
    assert c.get("/api/search-config").json()["enabled_sources"] == ["linkedin"]


def test_the_schema_double_rejects_what_prod_rejects():
    """Soundness of the double (CLAUDE.md #6): without the column the upsert
    really is refused, so a pass above is not the double agreeing with itself."""
    fake, _ = _api(PROD_COLUMNS)
    with pytest.raises(Exception, match="PGRST204"):
        fake.table("user_search_configs").upsert(
            {"user_id": USER, "enabled_sources": []}, on_conflict="user_id").execute()
