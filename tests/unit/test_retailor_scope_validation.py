"""An unrecognised regenerate scope must not widen to the whole pipeline.

`re_tailor_job` computes `resume_only = (body or {}).get("scope") == "resume"`.
Every value that is not exactly `"resume"` therefore means False, and False
means run everything — TailorResume, CompileResume, GenerateCoverLetter,
CompileCoverLetter, FindContacts.

So a typo, a renamed button or a future scope nobody wired up does not fail.
It silently re-tailors and recompiles a résumé the caller never asked to touch.
A widening default dressed as a narrowing one, and exactly the shape CLAUDE.md
#13 describes: the check fires and changes nothing.

KNOWN AND DELIBERATE: `"cover"` is accepted and still runs the full pipeline.
There is no cover-letter-only route through the state machine —
`GenerateCoverLetter` dereferences `$.light_touch`, which only the Pass states
set, so skipping TailorResume needs a second Pass, and Pass-state edits caused
both the #126 and #132 outages. The previous résumé is archived to
`resume_versions` before any regenerate, so the cost is a wasted re-tailor
rather than lost work. The test below pins that this is a choice, not a gap
someone forgot.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

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
    """A client whose DB answers with one job, so the endpoint reaches the
    scope check rather than 404-ing first."""
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    chain = db.client.table.return_value
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.update.return_value = chain
    chain.insert.return_value = chain
    chain.execute.return_value = MagicMock(data=[{
        "job_id": "j-1", "job_hash": "h-1", "user_id": "user-1",
        "resume_version": 1, "title": "SRE", "company": "Acme",
    }])
    monkeypatch.setattr(app_module, "_db", db)
    # No state machine: the local branch is enough to reach the scope check,
    # and it keeps the test off boto3 entirely.
    monkeypatch.delenv("SINGLE_JOB_PIPELINE_ARN", raising=False)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com")
    yield TestClient(app_module.app, raise_server_exceptions=False)
    app_module.app.dependency_overrides.clear()


@pytest.mark.parametrize("scope", ["covers", "RESUME", "both", "", "all", "cover-letter"])
def test_an_unknown_scope_is_rejected_not_widened(client, scope):
    """Behavioural, not structural.

    The first version of this file asserted that the string "unknown scope"
    appeared in the source. `ast.unparse` still contains it when the guard is
    `if False:`, so BOTH mutations — removing the validation, and re-reading the
    body instead of the validated scope — survived. A test that cannot tell a
    live branch from a dead one is not testing the branch.
    """
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": scope})
    assert r.status_code == 400, (
        f"scope={scope!r} returned {r.status_code}; anything that is not "
        "'resume' means resume_only=False, which runs the FULL pipeline and "
        "re-tailors a resume the caller never asked to touch"
    )
    assert "unknown scope" in r.text


@pytest.mark.parametrize("scope", ["resume", "cover"])
def test_the_scopes_the_ui_sends_are_accepted(client, scope):
    """JobWorkspace.jsx sends exactly these two; rejecting one breaks a button.

    Asserts 202, not `!= 400`. The original `!= 400` is satisfied by a 500,
    and on 2026-10-08 that is exactly what it let through: a null-hash fix
    read `job` twelve lines above the query that defines it, so EVERY
    regenerate raised NameError -> 500. 1847 tests passed. The only red was
    this file's invalid-scope case returning 500 where it wanted 400 — the bug
    was caught by the wrong test, by accident.

    CLAUDE.md #2: a check that cannot distinguish "did the work" from "crashed"
    is not a check.
    """
    r = client.post("/api/pipeline/re-tailor/j-1", json={"scope": scope})
    assert r.status_code == 202, (
        f"scope={scope!r} returned {r.status_code}, not 202: {r.text[:300]}")


def test_no_scope_at_all_is_still_the_full_pipeline(client):
    """The documented default. Omitting the body is not the same as sending a
    value nobody recognises, and only the second is a mistake."""
    r = client.post("/api/pipeline/re-tailor/j-1", json={})
    assert r.status_code != 400


def test_the_ui_and_the_endpoint_agree_on_the_vocabulary():
    """If a new button appears, its scope must be validated too — otherwise it
    rejoins the silent-widening path."""
    ui = (ROOT / "web/src/pages/JobWorkspace.jsx").read_text()
    sent = set(re.findall(r"handleRegen\('(\w+)'\)", ui))
    assert sent <= {"resume", "cover"}, (
        f"the UI sends {sorted(sent)}, which the endpoint does not all accept")
    assert sent, "no handleRegen call found; retarget this test"
