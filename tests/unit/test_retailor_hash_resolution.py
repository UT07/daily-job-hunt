"""Regenerate must key on a hash that jobs_raw actually has.

Reported 2026-10-08: the user clicked Regenerate, waited, and got "Pipeline
execution failed". The execution had run for three minutes and died inside
TailorResume with `Job None not found in jobs_raw`.

Rows created by `_find_or_create_job` — every manually added job — carry
`canonical_hash` and leave `job_hash` NULL, because `jobs.job_hash` has a
foreign key into `jobs_raw` that a job being created cannot yet satisfy.
Regenerate read `job["job_hash"]` and passed the NULL straight into the state
machine. `/api/pipeline/run-single` upserts jobs_raw under `canonical_hash`, so
the usable key was sitting in the next column.

Seven of this user's rows had a NULL `job_hash`; one had a canonical_hash in
jobs_raw. The other six are unrecoverable, and the point of the precheck is
that they now fail in 50ms with a sentence that says what to do, instead of
three minutes later with a constant Cause string.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


def _client(monkeypatch, job_row, jobs_raw_rows):
    """A double that answers per-table, so `jobs` and `jobs_raw` can disagree.

    CLAUDE.md #6: the shared-chain MagicMock used elsewhere returns the SAME
    data for every table, which would make the jobs_raw precheck pass for any
    hash at all — a double that cannot fail the way the bug fails.
    """
    import app as app_module
    from auth import AuthUser, get_current_user

    def table(name):
        chain = MagicMock()
        for m in ("select", "eq", "update", "insert", "upsert", "limit", "order", "single"):
            getattr(chain, m).return_value = chain
        if name == "jobs":
            chain.execute.return_value = MagicMock(data=[job_row])
        elif name == "jobs_raw":
            chain.execute.return_value = MagicMock(data=jobs_raw_rows)
        else:
            chain.execute.return_value = MagicMock(data=[])
        return chain

    db = MagicMock()
    db.client.table.side_effect = table
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.delenv("SINGLE_JOB_PIPELINE_ARN", raising=False)

    enqueued = {}
    monkeypatch.setattr(app_module, "_enqueue_task",
                        lambda tid, uid, kind, payload: enqueued.update(payload))

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com")
    client = TestClient(app_module.app, raise_server_exceptions=False)
    return client, enqueued


ROW = {"job_id": "j-1", "user_id": "user-1", "resume_version": 1,
       "title": "SRE", "company": "Acme"}


def test_a_null_job_hash_falls_back_to_canonical_hash(monkeypatch):
    """The production shape: manual job, NULL job_hash, usable canonical_hash."""
    client, enqueued = _client(
        monkeypatch,
        {**ROW, "job_hash": None, "canonical_hash": "b3026c307c74"},
        [{"job_hash": "b3026c307c74"}],
    )
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": "resume"})
    assert r.status_code == 202, r.text
    assert enqueued.get("job_hash") == "b3026c307c74", (
        "the pipeline was handed %r — a NULL here is the three-minute "
        "'Job None not found in jobs_raw' failure" % (enqueued.get("job_hash"),))


def test_job_hash_wins_when_both_are_present(monkeypatch):
    """canonical_hash is the FALLBACK, not an override. Scraped rows have both,
    and jobs_raw is keyed by job_hash for those."""
    client, enqueued = _client(
        monkeypatch,
        {**ROW, "job_hash": "real-hash", "canonical_hash": "other-hash"},
        [{"job_hash": "real-hash"}],
    )
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": "resume"})
    assert r.status_code == 202, r.text
    assert enqueued.get("job_hash") == "real-hash"


def test_no_usable_hash_fails_fast_with_an_actionable_message(monkeypatch):
    """Both columns NULL. Must 409 in milliseconds, not 202 into a doomed run."""
    client, enqueued = _client(
        monkeypatch, {**ROW, "job_hash": None, "canonical_hash": None}, [])
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": "resume"})
    assert r.status_code == 409, (
        f"returned {r.status_code}; accepting this starts an execution that "
        "cannot succeed and reports a constant Cause three minutes later")
    assert not enqueued, "a doomed run was started anyway"
    assert "job description" in r.text.lower()


def test_a_hash_jobs_raw_has_never_heard_of_fails_fast(monkeypatch):
    """The six unrecoverable rows: a hash exists, but no posting text does.

    This is the check that needs the per-table double. With one shared chain
    the jobs_raw lookup returns the jobs row and this passes for any input.
    """
    client, enqueued = _client(
        monkeypatch,
        {**ROW, "job_hash": None, "canonical_hash": "orphan-hash"},
        [],  # jobs_raw has nothing
    )
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": "resume"})
    assert r.status_code == 409, f"returned {r.status_code}: {r.text[:200]}"
    assert not enqueued
    assert "never stored" in r.text


def test_a_failed_precheck_does_not_block_a_valid_run(monkeypatch):
    """The precheck is a courtesy, not a gate. If the jobs_raw lookup itself
    errors, the run proceeds — refusing would turn a transient DB blip into a
    broken button."""
    import app as app_module
    from auth import AuthUser, get_current_user

    def table(name):
        chain = MagicMock()
        for m in ("select", "eq", "update", "insert", "upsert", "limit"):
            getattr(chain, m).return_value = chain
        if name == "jobs_raw":
            chain.execute.side_effect = RuntimeError("connection reset")
        else:
            chain.execute.return_value = MagicMock(
                data=[{**ROW, "job_hash": "h-1"}])
        return chain

    db = MagicMock()
    db.client.table.side_effect = table
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.delenv("SINGLE_JOB_PIPELINE_ARN", raising=False)
    enqueued = {}
    monkeypatch.setattr(app_module, "_enqueue_task",
                        lambda tid, uid, kind, payload: enqueued.update(payload))
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com")
    client = TestClient(app_module.app, raise_server_exceptions=False)
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": "resume"})
    app_module.app.dependency_overrides.clear()

    assert r.status_code == 202, r.text
    assert enqueued.get("job_hash") == "h-1"
