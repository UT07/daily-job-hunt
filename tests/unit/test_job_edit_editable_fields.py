"""PATCH /api/dashboard/jobs/{id} must never silently discard a field.

The defect, verified on main at ab7ae3f:

  * `startEditing()` in web/src/pages/JobWorkspace.jsx seeded `editFields` with
    title, company, location AND apply_url.
  * `handleSave()` PATCHed all four, then merged all four into local state with
    `setJob((prev) => ({ ...prev, ...editFields }))`, then set
    `{type:'success', message:'Job updated.'}`.
  * `update_job` filtered the body to `_EDITABLE_FIELDS`, which did not contain
    title or company. The request still returned 200 because `update_data` was
    non-empty from `location`/`apply_url` alone.

So the user edited the title, saw the new title, read "Job updated." and got the
old title back on reload — CLAUDE.md rule 2, a status that cannot distinguish
"did the work" from "did part of the work".

The fix: reject a non-editable key with a 400 that names it, rather than
dropping it. `title`/`company` stay non-editable on purpose — see the comment on
`_EDITABLE_FIELDS` in app.py; merge_dedup.py builds its cross-source dedup key
from the stored `jobs.company` + `jobs.title`.

Note on the doubles (CLAUDE.md rule 6): the two failure modes here — "rejected
with a 400" and "accepted but the field was dropped" — are distinguished by
asserting on `chain.update`, the actual write. A test that only checked the
status code would pass under the bug for the mixed-body case, because the bug
also returns 200 for `{title, location}`; it just returns 200 having written
only `location`.
"""
from __future__ import annotations

import os
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def client(monkeypatch):
    """TestClient with `app._db` replaced by a MagicMock.

    Nothing in this module may reach a real Supabase project: `_db` is patched
    before any request is made, and every assertion is against the mock's
    recorded calls.
    """
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    monkeypatch.setattr(app_module, "_db", db)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com",
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def _stub_jobs_table(db, returned):
    """Wire `.table('jobs').update(...).eq(...).eq(...).execute()` to `returned`."""
    chain = MagicMock()
    chain.update.return_value = chain
    chain.insert.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value = MagicMock(data=returned)
    db.client.table.return_value = chain
    return chain


# ---------------------------------------------------------------------------
# The reported defect
# ---------------------------------------------------------------------------

def test_title_and_company_are_rejected_not_dropped(client):
    """The exact body the Overview form used to send.

    Under the bug this returned 200 with `location` written and title/company
    silently gone. It must now be a 400 that names both dropped fields, and it
    must not write anything at all: a partial write reported as success is the
    defect, so half-applying the body is not an acceptable outcome either.
    """
    c, db = client
    chain = _stub_jobs_table(db, [{"job_id": "job-1"}])

    r = c.patch(
        "/api/dashboard/jobs/job-1",
        json={
            "title": "Renamed Title",
            "company": "Renamed Co",
            "location": "Dublin",
            "apply_url": "https://example.com/apply",
        },
    )

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "title" in detail and "company" in detail, detail
    # The whole point: no write happened, so nothing can have half-succeeded.
    chain.update.assert_not_called()


def test_title_only_body_is_rejected(client):
    """A title-only edit must not report success for a no-op.

    Under the bug this hit `if not update_data` and 400'd with "At least one
    editable field required" — accidentally loud, but for the wrong reason, and
    it never named `title`. The message must say which field was refused.
    """
    c, db = client
    chain = _stub_jobs_table(db, [{"job_id": "job-1"}])

    r = c.patch("/api/dashboard/jobs/job-1", json={"title": "Renamed Title"})

    assert r.status_code == 400, r.text
    assert "title" in r.json()["detail"]
    chain.update.assert_not_called()


def test_unknown_field_is_rejected_by_name(client):
    """The durable half of the fix: the next field added to the form.

    Whatever it is called, it must fail loudly here rather than be filtered out
    of the body while the endpoint answers 200.
    """
    c, db = client
    chain = _stub_jobs_table(db, [{"job_id": "job-1"}])

    r = c.patch(
        "/api/dashboard/jobs/job-1",
        json={"location": "Dublin", "notes": "some future field"},
    )

    assert r.status_code == 400, r.text
    assert "notes" in r.json()["detail"]
    chain.update.assert_not_called()


# ---------------------------------------------------------------------------
# Nothing that worked may stop working
# ---------------------------------------------------------------------------

def test_editable_fields_are_still_written(client):
    """location + apply_url is the rest of the form's body and must still save."""
    c, db = client
    chain = _stub_jobs_table(
        db,
        [{"job_id": "job-1", "location": "Dublin", "apply_url": "https://example.com/apply"}],
    )

    r = c.patch(
        "/api/dashboard/jobs/job-1",
        json={"location": "Dublin", "apply_url": "https://example.com/apply"},
    )

    assert r.status_code == 200, r.text
    chain.update.assert_called_once_with(
        {"location": "Dublin", "apply_url": "https://example.com/apply"},
    )


def test_status_dropdown_body_is_still_accepted(client):
    """web/src/components/StatusDropdown.jsx sends exactly {application_status}.

    It is the only other caller of this endpoint, so a stricter body check is
    the sort of change that could break it. It must not.
    """
    c, db = client
    chain = _stub_jobs_table(db, [{"job_id": "job-1", "application_status": "Applied"}])

    r = c.patch("/api/dashboard/jobs/job-1", json={"application_status": "Applied"})

    assert r.status_code == 200, r.text
    chain.update.assert_called_once_with({"application_status": "Applied"})


def test_invalid_status_still_rejected(client):
    """The pre-existing status whitelist must keep firing."""
    c, db = client
    chain = _stub_jobs_table(db, [{"job_id": "job-1"}])

    r = c.patch("/api/dashboard/jobs/job-1", json={"application_status": "Ghosted"})

    assert r.status_code == 400, r.text
    assert "Invalid status" in r.json()["detail"]
    chain.update.assert_not_called()


# ---------------------------------------------------------------------------
# The two lists must not drift apart again (CLAUDE.md rule 10)
# ---------------------------------------------------------------------------

def test_form_editable_fields_are_a_subset_of_the_backend_set():
    """The form's EDITABLE_JOB_FIELDS must be a subset of app._EDITABLE_FIELDS.

    This is the guard that stops the defect recurring by drift rather than by
    omission. The bug existed because two lists in two languages disagreed and
    nothing compared them. Parsing the JSX is ugly but it is the only way to
    compare them at all; `tests/unit/test_regenerate_scope.py` reads the same
    file the same way.
    """
    import app as app_module

    source = pathlib.Path("web/src/pages/JobWorkspace.jsx").read_text()

    block = re.search(
        r"const EDITABLE_JOB_FIELDS = \[(.*?)\];", source, re.DOTALL,
    )
    assert block, "EDITABLE_JOB_FIELDS not found in JobWorkspace.jsx — has it been renamed?"
    form_fields = set(re.findall(r"name:\s*'([^']+)'", block.group(1)))
    assert form_fields, "parsed no field names out of EDITABLE_JOB_FIELDS"

    unsupported = form_fields - set(app_module._EDITABLE_FIELDS)
    assert not unsupported, (
        f"The Overview edit form offers {sorted(unsupported)}, which "
        f"PATCH /api/dashboard/jobs/{{id}} refuses. Either add them to "
        f"app._EDITABLE_FIELDS or take them out of the form."
    )


def test_form_does_not_offer_title_or_company_for_edit():
    """title/company must stay out of the form's editable list.

    Not style: merge_dedup.py builds `existing_dedup_keys` from the stored
    jobs.company + jobs.title, and that key is the only tier that catches the
    same posting scraped from two boards (their differing descriptions give them
    different job_hash values). A rename here reopens that duplicate.

    Measured read-only against production 2026-09-30, not argued from the code:
    in `jobs_raw`, 76 company|title keys cover multiple job_hash values across
    more than one source; in post-dedup `jobs` that figure is 0, so the key
    removed all 76. One of them is 'treqs|backend software engineer' — the very
    example named in CLAUDE.md's Phase 2.7 Priority-1 backlog item.
    """
    source = pathlib.Path("web/src/pages/JobWorkspace.jsx").read_text()
    block = re.search(r"const EDITABLE_JOB_FIELDS = \[(.*?)\];", source, re.DOTALL)
    assert block, "EDITABLE_JOB_FIELDS not found in JobWorkspace.jsx"
    form_fields = set(re.findall(r"name:\s*'([^']+)'", block.group(1)))

    assert "title" not in form_fields
    assert "company" not in form_fields
