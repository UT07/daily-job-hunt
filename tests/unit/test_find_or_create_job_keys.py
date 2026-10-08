"""Save & Score silently created nothing for three weeks of key mismatch.

`_find_or_create_job` read `payload["job_title"]` and
`payload["job_description"]`. `/api/score` passes `{"title", "description"}`.
So every Save & Score arrived with an EMPTY description, tripped the
"refuse to create a stub" guard, and returned `""`.

The endpoint then ran `UPDATE jobs ... WHERE job_id = ''`, which matches nothing
and raises nothing, set `saved = True` unconditionally, and answered HTTP 200.
From the browser it looked like it worked.

Reported 2026-10-08 as "new jobs are not saving". Production evidence, three
attempts at one Accenture role:

    POST /api/score 200
    WARNING Refusing to create a job row for Accenture/Software Engineer
            with an empty description
    PATCH .../rest/v1/jobs?job_id=eq.   HTTP/2 200 OK

Note the title ALSO defaulted to "Software Engineer" — the same mismatch
showing twice in one log line, which is what made it findable.

Both spellings are accepted because the two callers genuinely disagree and
neither is wrong: `/api/score` sends `title`/`description`, `_process_task`
forwards a task payload using `job_title`/`job_description`.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JD = "We are hiring a Site Reliability Engineer. " * 12


@pytest.fixture
def app_mod(monkeypatch):
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        import app as app_module
        db = MagicMock()
        chain = db.client.table.return_value
        for m in ("select", "eq", "update", "insert", "range", "limit"):
            getattr(chain, m).return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = MagicMock(data=None)   # nothing exists yet
        # INSERT answers with the row it wrote (returning=representation); an
        # insert answering data=None is one that wrote nothing.
        chain.insert.side_effect = lambda row: MagicMock(
            execute=MagicMock(return_value=MagicMock(data=[row])))
        monkeypatch.setattr(app_module, "_db", db)
        yield app_module, chain


class TestBothSpellingsCreateARow:
    def test_the_score_endpoints_spelling(self, app_mod):
        """`title` / `description` — what /api/score sends."""
        app_module, chain = app_mod
        job_id = app_module._find_or_create_job("u-1", {
            "company": "Accenture", "title": "Software Engineer", "description": JD})
        assert job_id, "returned '' — the row was never created and the UPDATE matched nothing"
        assert chain.insert.called

    def test_the_task_payloads_spelling(self, app_mod):
        """`job_title` / `job_description` — what _process_task forwards."""
        app_module, chain = app_mod
        job_id = app_module._find_or_create_job("u-1", {
            "company": "Accenture", "job_title": "SRE", "job_description": JD})
        assert job_id
        assert chain.insert.called

    def test_the_title_is_not_silently_defaulted(self, app_mod):
        """The same mismatch cost the title too: every dropped row logged
        "Accenture/Software Engineer" for a job that was not called that."""
        app_module, chain = app_mod
        app_module._find_or_create_job("u-1", {
            "company": "Accenture", "title": "Staff Platform Engineer", "description": JD})
        row = chain.insert.call_args[0][0]
        assert row["title"] == "Staff Platform Engineer", (
            f"title fell back to a default: {row['title']!r}")


class TestTheStubGuardStillWorks:
    def test_a_genuinely_empty_description_is_still_refused(self, app_mod):
        """The guard is right — a row with no description can never acquire an
        artifact and fails the deploy gate forever. It must keep firing for the
        case it was written for."""
        app_module, chain = app_mod
        assert app_module._find_or_create_job("u-1", {
            "company": "Acme", "title": "SRE", "description": "   "}) == ""
        assert not chain.insert.called

    def test_no_description_under_either_key_is_refused(self, app_mod):
        app_module, chain = app_mod
        assert app_module._find_or_create_job("u-1", {"company": "Acme"}) == ""
        assert not chain.insert.called


# ---------------------------------------------------------------------------
# `saved` must mean saved
# ---------------------------------------------------------------------------
# The key mismatch above made the row disappear. THIS is what made it
# invisible: `saved = True` was set unconditionally after an UPDATE that ran as
# `job_id=eq.`, matched nothing and raised nothing. The browser got HTTP 200
# with every score filled in, so the only symptom was a job that never appeared
# on the dashboard — hours later, with nothing to connect it to.
#
# CLAUDE.md #2: a status that cannot distinguish "did the work" from "did
# nothing" is a lie. Fixing the mismatch without fixing this would leave the
# next mismatch just as silent.

def test_saved_is_false_when_no_row_was_created(monkeypatch):
    from fastapi.testclient import TestClient

    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        import app as app_module
        from auth import AuthUser, get_current_user

        db = MagicMock()
        chain = db.client.table.return_value
        for m in ("select", "eq", "update", "insert", "maybe_single"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = MagicMock(data=None)
        monkeypatch.setattr(app_module, "_db", db)

        # The failure under test: the row could not be created.
        monkeypatch.setattr(app_module, "_find_or_create_job", lambda *a, **k: "")

        # /api/score scores through score_single_job_deterministic since
        # 2026-10-08 (medians, because one sample measured a 15-point spread).
        monkeypatch.setattr(app_module, "score_single_job_deterministic",
                            lambda *a, **k: {
                                "match_score": 82, "ats_score": 80,
                                "hiring_manager_score": 84,
                                "tech_recruiter_score": 82, "reasoning": "ok"})
        monkeypatch.setattr(app_module, "_ai_client", MagicMock())
        # _resumes is loaded from config.yaml at import; empty under test,
        # and the handler 400s on an unknown resume_type before it ever
        # reaches the save path this test is about.
        monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "resume.tex"})
        monkeypatch.setattr(app_module, "_posthog", None)

        app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
            id="u-1", email="u@example.test")
        client = TestClient(app_module.app, raise_server_exceptions=False)
        r = client.post("/api/score", json={
            "job_description": JD, "job_title": "Software Engineer",
            "company": "Accenture", "resume_type": "sre_devops"})
        app_module.app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("saved") is False, (
        "the endpoint reported saved=True for a job it did not create — the "
        "exact reason three dropped Accenture submissions looked like successes")
    assert not body.get("job_id")
