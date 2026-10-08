"""`GET /api/tasks/{task_id}` must only return the caller's own task.

Audited 2026-10-08: `_load_task` selected by `task_id` alone, so any signed-in
user holding another user's task id read its status, result and error. Task
results carry generated résumé content and S3 keys. Another user's task is
now indistinguishable from a missing one: 404.

The Supabase double filters on whatever `.eq()` it is given, so a test only
passes if the code asked for the right owner (checked for soundness below).
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROWS = [
    {"task_id": "task-a", "user_id": "user-a", "status": "done", "result": {"pdf": "a.pdf"}, "error": None},
    {"task_id": "task-b", "user_id": "user-b", "status": "done", "result": {"pdf": "b.pdf"}, "error": None},
]


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return _Result(self._rows[0] if self._rows else None)


class _Client:
    def table(self, name):
        assert name == "pipeline_tasks"
        return _Query(ROWS)


class FakeDB:
    client = _Client()


@pytest.fixture
def as_user(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    monkeypatch.setattr(app_module, "_db", FakeDB())

    def _client(user_id):
        app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=user_id, email="x@y.z")
        return TestClient(app_module.app)

    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield _client
    app_module.app.dependency_overrides.clear()


def test_owner_reads_their_task(as_user):
    r = as_user("user-a").get("/api/tasks/task-a")
    assert r.status_code == 200
    assert r.json() == {"status": "done", "result": {"pdf": "a.pdf"}}


def test_another_users_task_is_404(as_user):
    r = as_user("user-b").get("/api/tasks/task-a")
    assert r.status_code == 404
    assert "a.pdf" not in r.text


def test_missing_task_is_404(as_user):
    assert as_user("user-a").get("/api/tasks/nope").status_code == 404


def test_the_double_filters():
    assert FakeDB.client.table("pipeline_tasks").select("*").eq("task_id", "task-a").execute().data["user_id"] == "user-a"
    assert FakeDB.client.table("pipeline_tasks").select("*").eq("task_id", "task-a").eq("user_id", "user-b").execute().data is None
