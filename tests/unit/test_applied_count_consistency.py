"""Regression tests for F5 — Applied count silently resets to 0.

Prior to fix/comprehensive-prod-health/dashboard-state, `db_client.get_job_stats`
computed `total_applied` from the *current* `jobs.application_status` only.
That was wrong for two reasons:

1. The PATCH `/api/dashboard/jobs/{id}` endpoint (used by the inline
   StatusDropdown) never wrote to `application_timeline`, so a status change
   from Applied → New silently dropped the funnel count to 0 even though
   the user really did apply once.
2. The funnel comment in code claimed "ever reached Applied" semantics, but
   the implementation summed the *current* `_APPLIED_FUNNEL` bucket only —
   a Applied → Withdrawn transition kept the count, but Applied → New
   reset it.

The fix:
- `update_job` PATCH now mirrors application_status changes into the
  `application_timeline` table.
- `get_job_stats` reads the timeline log and counts distinct jobs per stage,
  folding in the current `jobs.application_status` as a backstop for jobs
  whose history predates the mirror.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    monkeypatch.setattr(app_module, "_db", db)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com",
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def _stub_update(db, returned):
    """Wire `db.client.table('jobs').update(...).eq(...).eq(...).execute()` to return ``returned``."""
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value = MagicMock(data=returned)
    db.client.table.return_value = chain
    return chain


def test_patch_status_writes_timeline_row(client):
    """PATCH /api/dashboard/jobs/{id} with application_status must also insert
    into application_timeline so the funnel never drops to 0 on later changes."""
    c, db = client

    # First call (.table('jobs').update) returns the updated job; subsequent
    # call to .table('application_timeline').insert() must record the event.
    jobs_chain = MagicMock()
    jobs_chain.update.return_value = jobs_chain
    jobs_chain.eq.return_value = jobs_chain
    jobs_chain.execute.return_value = MagicMock(
        data=[{"job_id": "job-1", "application_status": "Applied"}],
    )

    timeline_chain = MagicMock()
    timeline_chain.insert.return_value = timeline_chain
    timeline_chain.execute.return_value = MagicMock(data=[])

    def _table(name):
        return jobs_chain if name == "jobs" else timeline_chain

    db.client.table.side_effect = _table

    resp = c.patch("/api/dashboard/jobs/job-1", json={"application_status": "Applied"})
    assert resp.status_code == 200, resp.text

    # Timeline insert called with the new status.
    timeline_chain.insert.assert_called_once()
    inserted = timeline_chain.insert.call_args.args[0]
    assert inserted["status"] == "Applied"
    assert inserted["job_id"] == "job-1"
    assert inserted["user_id"] == "user-1"


def test_patch_non_status_field_skips_timeline(client):
    """PATCHing only `location` must NOT insert a timeline row."""
    c, db = client

    jobs_chain = MagicMock()
    jobs_chain.update.return_value = jobs_chain
    jobs_chain.eq.return_value = jobs_chain
    jobs_chain.execute.return_value = MagicMock(
        data=[{"job_id": "job-1", "location": "Dublin"}],
    )

    timeline_chain = MagicMock()
    timeline_chain.insert.return_value = timeline_chain
    timeline_chain.execute.return_value = MagicMock(data=[])

    def _table(name):
        return jobs_chain if name == "jobs" else timeline_chain

    db.client.table.side_effect = _table

    resp = c.patch("/api/dashboard/jobs/job-1", json={"location": "Dublin"})
    assert resp.status_code == 200, resp.text
    timeline_chain.insert.assert_not_called()


def test_get_job_stats_counts_jobs_that_ever_reached_applied():
    """get_job_stats must count a job as Applied if it has ANY 'Applied'
    timeline event, regardless of what its current application_status is.

    Reproduces F5: applying then changing status to New (a non-funnel state)
    should still keep total_applied ≥ 1.
    """
    from db_client import SupabaseClient

    sb = SupabaseClient.__new__(SupabaseClient)  # bypass __init__

    jobs_data = [
        {"match_score": 80, "application_status": "New"},
        {"match_score": 50, "application_status": "New"},
    ]
    jobs_with_status = [
        {"job_id": "job-1", "application_status": "New"},
        {"job_id": "job-2", "application_status": "New"},
    ]
    timeline_data = [
        # job-1 was Applied at one point and is now back to New
        {"job_id": "job-1", "status": "Applied"},
        {"job_id": "job-1", "status": "New"},
    ]

    # Build a faux Supabase client whose table().select().eq().execute()
    # returns the right slice of data based on call order.
    call_log = []

    class _Chain:
        def __init__(self, data):
            self._data = data

        def select(self, *a, **kw):
            return self

        def eq(self, *a, **kw):
            return self

        def execute(self):
            return MagicMock(data=self._data)

    def _table(name):
        call_log.append(name)
        if name == "jobs":
            # First call uses (match_score, application_status), second uses (job_id, application_status)
            jobs_calls = sum(1 for n in call_log if n == "jobs")
            return _Chain(jobs_data if jobs_calls == 1 else jobs_with_status)
        if name == "application_timeline":
            return _Chain(timeline_data)
        return _Chain([])

    sb.client = MagicMock()
    sb.client.table.side_effect = _table

    stats = sb.get_job_stats("user-1")

    assert stats["total_applied"] == 1, (
        f"job-1 was Applied per the timeline, current status={jobs_with_status[0]['application_status']!r}; "
        f"total_applied must reflect the funnel history. Got {stats['total_applied']}."
    )
    # The current-status counter still reports New for both rows.
    assert stats["jobs_by_status"].get("New") == 2


def test_get_job_stats_zero_when_no_one_ever_applied():
    """Sanity: total_applied is 0 when no timeline event nor current status reaches the funnel."""
    from db_client import SupabaseClient

    sb = SupabaseClient.__new__(SupabaseClient)

    jobs_data = [{"match_score": 80, "application_status": "New"}]
    jobs_with_status = [{"job_id": "job-1", "application_status": "New"}]

    class _Chain:
        def __init__(self, data):
            self._data = data

        def select(self, *a, **kw):
            return self

        def eq(self, *a, **kw):
            return self

        def execute(self):
            return MagicMock(data=self._data)

    seen = []

    def _table(name):
        seen.append(name)
        if name == "jobs":
            jobs_calls = sum(1 for n in seen if n == "jobs")
            return _Chain(jobs_data if jobs_calls == 1 else jobs_with_status)
        if name == "application_timeline":
            return _Chain([])
        return _Chain([])

    sb.client = MagicMock()
    sb.client.table.side_effect = _table

    stats = sb.get_job_stats("user-1")
    assert stats["total_applied"] == 0
    assert stats["total_interviewing"] == 0
    assert stats["total_offers"] == 0
    assert stats["total_rejected"] == 0
