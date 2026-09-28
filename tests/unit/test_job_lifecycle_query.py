"""Regression tests for how the age-based lifecycle reaches the query.

`shared/job_lifecycle.py` and `tests/unit/test_job_lifecycle.py` already pin
the *rule*. These pin the two places the rule was being lost on its way into
PostgREST — both found by querying prod on 2026-09-28, both invisible to a
test that only exercised the pure functions:

1. The documented ``lifecycle="not_archived"`` default lived inside
   ``if filters:`` in ``db_client.get_jobs``. An empty/None filters dict is
   falsy, so a caller that passed no filters got no lifecycle clause at all —
   an unfiltered ``GET /api/dashboard/jobs`` returned all 1,251 rows, 1,164 of
   them archived. The docstring said otherwise, which is exactly the failure
   mode this repo keeps hitting: a comment asserting behaviour the code does
   not have.

2. ``hide_expired`` was a bare ``.eq("is_expired", False)``. Every one of the
   38 engaged rows in prod (Applied / Withdrawn / Rejected) carries
   ``is_expired = True``, because the posting 404'd months after the user
   applied. So the age rule correctly exempted them and this filter — on by
   default in the dashboard — deleted them one line later. Net effect: the
   owner's "unless I applied to it" exemption was unobservable in the UI.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.job_lifecycle import ENGAGED_STATUSES


def _mock_client():
    """A SupabaseClient whose query builder records every call."""
    from db_client import SupabaseClient

    sb = SupabaseClient.__new__(SupabaseClient)
    chain = MagicMock()
    for method in ("select", "eq", "gte", "lt", "ilike", "neq", "in_",
                   "or_", "order", "range", "filter"):
        getattr(chain, method).return_value = chain
    chain.not_.in_.return_value = chain
    chain.execute.return_value = MagicMock(data=[], count=0)

    sb.client = MagicMock()
    sb.client.table.return_value = chain
    return sb, chain


def _or_clauses(chain):
    return [c.args[0] for c in chain.or_.call_args_list if c.args]


# --------------------------------------------------------------------------
# 1. The default applies even with no filters
# --------------------------------------------------------------------------

@pytest.mark.parametrize("filters", [None, {}], ids=["filters=None", "filters={}"])
def test_not_archived_default_applies_when_no_filters_are_passed(filters):
    """The default must not be conditional on some *other* filter existing."""
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters=filters)

    clauses = _or_clauses(chain)
    assert any("first_seen.gte." in c for c in clauses), (
        "get_jobs applied no lifecycle clause with empty filters — archived "
        f"rows would be returned. or_ calls: {clauses!r}"
    )


def test_default_clause_exempts_engaged_rows_and_null_timestamps():
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={})

    lifecycle_clause = next(c for c in _or_clauses(chain) if "first_seen.gte." in c)
    assert "first_seen.is.null" in lifecycle_clause, (
        "a row with no first_seen must never be aged out"
    )
    for status in ENGAGED_STATUSES:
        assert status in lifecycle_clause, f"{status} is not exempt from archiving"


def test_lifecycle_all_applies_no_age_clause():
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"lifecycle": "all"})

    assert not any("first_seen" in c for c in _or_clauses(chain))
    assert not chain.lt.called


@pytest.mark.parametrize("lifecycle", ["stale", "archived"])
def test_stale_and_archived_exclude_engaged_rows(lifecycle):
    """Engaged rows belong in the active list, never on the past shelf."""
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"lifecycle": lifecycle})

    assert chain.lt.called, f"{lifecycle} must bound first_seen from above"
    excluded = chain.not_.in_.call_args_list
    assert excluded, f"{lifecycle} did not exclude engaged statuses"
    column, statuses = excluded[0].args
    assert column == "application_status"
    assert set(statuses) == set(ENGAGED_STATUSES)


def test_stale_is_bounded_on_both_sides():
    """Stale is a band (14-30), not 'older than 14' — otherwise it re-shows
    everything the archive rule just removed."""
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"lifecycle": "stale"})

    assert any(c.args[0] == "first_seen" for c in chain.lt.call_args_list)
    assert any(c.args[0] == "first_seen" for c in chain.gte.call_args_list)


# --------------------------------------------------------------------------
# 2. hide_expired must not erase the user's applications
# --------------------------------------------------------------------------

def test_hide_expired_exempts_engaged_rows():
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"hide_expired": True})

    expiry_clause = next(
        (c for c in _or_clauses(chain) if "is_expired" in c), None
    )
    assert expiry_clause is not None, (
        "hide_expired used a bare equality filter; every Applied/Withdrawn/"
        "Rejected row in prod is is_expired=True and would be erased"
    )
    assert "is_expired.eq.false" in expiry_clause
    for status in ENGAGED_STATUSES:
        assert status in expiry_clause, f"{status} is not exempt from hide_expired"


def test_hide_expired_still_filters_unengaged_expired_rows():
    """The exemption must not degrade into 'show everything'."""
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"hide_expired": True})

    assert any("is_expired.eq.false" in c for c in _or_clauses(chain))


def test_hide_expired_absent_means_no_expiry_clause():
    sb, chain = _mock_client()
    sb.get_jobs("user-1", filters={"lifecycle": "all"})

    assert not any("is_expired" in c for c in _or_clauses(chain))


# --------------------------------------------------------------------------
# 3. The endpoint forwards the param
# --------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        import app as app_module
        from auth import AuthUser, get_current_user

        db = MagicMock()
        db.get_jobs.return_value = ([], 0)
        monkeypatch.setattr(app_module, "_db", db)
        app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
            id="user-1", email="u@example.com",
        )
        yield TestClient(app_module.app), db
        app_module.app.dependency_overrides.clear()


def test_endpoint_forwards_lifecycle_stale(api):
    c, db = api
    assert c.get("/api/dashboard/jobs?lifecycle=stale").status_code == 200
    assert db.get_jobs.call_args.kwargs["filters"]["lifecycle"] == "stale"


def test_endpoint_omits_lifecycle_when_not_given(api):
    """Absent means 'let get_jobs apply its own not_archived default' — the
    endpoint must not substitute a different one of its own."""
    c, db = api
    assert c.get("/api/dashboard/jobs").status_code == 200
    assert "lifecycle" not in db.get_jobs.call_args.kwargs["filters"]
