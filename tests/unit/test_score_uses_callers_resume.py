"""Save & Score must score the CALLER'S résumé, the one tailoring will use.

`_score_fresh` scored against `_resumes[req.resume_type]`: the .tex files
bundled in the repo and named in config.yaml, which are the owner's résumé.
Every other user's job was scored against someone else's CV, while Generate
Resume tailored from their own upload. The number on the card described a
document nobody was going to send.

The score path now reads the base résumé through
`shared.resume_format.fetch_tailorable_resume`, the one validated selector
tailor_resume, score_batch and generate_cover_letter already share (CLAUDE.md
#10: three readers of user_resumes, one validated, is how a PDF upload broke
tailoring on 2026-09-28). A user with no tailorable upload falls back to the
bundled résumé for `resume_type`, and the response and the stored row say so.

Reuse is the dangerous half. `_stored_score` hands back a stored number when
`matched_resume` matches, so `matched_resume` now identifies the DOCUMENT: an
upload is recorded as `<resume_key>@<sha256[:8]>`. A score against the bundled
résumé is not reused after an upload, and a score against last month's upload
is not reused after this month's, even though both are `resume_key='default'`
(upload_resume upserts on (user_id, resume_key), so the key never changes).

Runs over the PostgREST double, which applies filters and ordering. The
scorer is patched; no model is called.
"""
import hashlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs and incident response. " * 3)
BODY = {"job_description": JD, "job_title": "Site Reliability Engineer",
        "company": "Acme", "resume_type": "sre_devops"}

BUNDLED = "\\documentclass{article}\\begin{document}OWNER'S RESUME\\end{document}"
MINE = "\\documentclass{article}\\begin{document}CALLER'S RESUME\\end{document}"
MINE_V2 = "\\documentclass{article}\\begin{document}CALLER'S RESUME v2\\end{document}"


def _label(tex, key="default"):
    return f"{key}@{hashlib.sha256(tex.encode()).hexdigest()[:8]}"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


@pytest.fixture
def world(monkeypatch, inline_tasks):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = FakeSupabase({"jobs": [], "jobs_raw": [], "user_resumes": [], "users": []})
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": BUNDLED})
    monkeypatch.setattr(app_module, "_posthog", None)
    seen = []

    def fake_score(job, resume_tex, **kw):
        seen.append(resume_tex)
        return {"match_score": 82, "ats_score": 80, "hiring_manager_score": 84,
                "tech_recruiter_score": 82, "reasoning": "fits"}

    monkeypatch.setattr(app_module, "score_single_job_deterministic", fake_score)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.test")
    yield TestClient(app_module.app, raise_server_exceptions=False), db, seen, inline_tasks
    app_module.app.dependency_overrides.clear()


def _press(client, tasks, **extra):
    r = client.post("/api/score", json={**BODY, **extra})
    if r.status_code == 200:
        return r.json()
    assert r.status_code == 202, r.text
    return tasks[r.json()["task_id"]]["result"]


def _upload(db, tex, created_at, key="default", user="user-1"):
    db.tables["user_resumes"].append(
        {"user_id": user, "resume_key": key, "tex_content": tex, "created_at": created_at})


def test_the_callers_upload_is_what_gets_scored(world):
    client, db, seen, tasks = world
    _upload(db, MINE, "2026-10-01")
    out = _press(client, tasks)
    assert seen == [MINE], "scored against the bundled owner's résumé, not the caller's"
    assert out["resume_source"] == "upload"
    assert out["matched_resume"] == _label(MINE)
    row = db.rows("jobs", user_id="user-1")[0]
    assert row["matched_resume"] == _label(MINE), "the stored row does not say which résumé"


def test_another_users_upload_is_never_used(world):
    client, db, seen, tasks = world
    _upload(db, MINE, "2026-10-01", user="someone-else")
    out = _press(client, tasks)
    assert seen == [BUNDLED]
    assert out["resume_source"] == "bundled"


def test_the_validated_selector_skips_a_newer_non_latex_upload(world):
    """The same row the tailorer would pick: newest VALID LaTeX, not newest."""
    client, db, seen, tasks = world
    _upload(db, MINE, "2026-09-01")
    _upload(db, "plain text extracted from a PDF", "2026-10-01", key="pdf")
    out = _press(client, tasks)
    assert seen == [MINE]
    assert out["matched_resume"] == _label(MINE)


def test_no_upload_falls_back_to_the_bundled_resume_and_says_so(world):
    client, db, seen, tasks = world
    out = _press(client, tasks)
    assert seen == [BUNDLED]
    assert out["resume_source"] == "bundled"
    assert out["matched_resume"] == "sre_devops"


def test_a_bundled_score_is_not_reused_after_an_upload(world):
    client, db, seen, tasks = world
    first = _press(client, tasks)
    assert first["resume_source"] == "bundled"
    _upload(db, MINE, "2026-10-01")
    second = _press(client, tasks)
    assert second["reused"] is False, "a score against the owner's résumé was reused"
    assert seen == [BUNDLED, MINE]
    assert second["matched_resume"] == _label(MINE)


def test_the_same_upload_is_reused_without_a_model_call(world):
    client, db, seen, tasks = world
    _upload(db, MINE, "2026-10-01")
    _press(client, tasks)
    again = _press(client, tasks)
    assert again["reused"] is True
    assert again["resume_source"] == "upload"
    assert again["matched_resume"] == _label(MINE)
    assert seen == [MINE], "the model was asked again for an unchanged résumé"


def test_an_edited_upload_under_the_same_key_is_not_reused(world):
    """upload_resume upserts on (user_id, resume_key), so a re-upload keeps
    resume_key='default'. The key alone would reuse the old score forever."""
    client, db, seen, tasks = world
    _upload(db, MINE, "2026-10-01")
    _press(client, tasks)
    db.tables["user_resumes"][0]["tex_content"] = MINE_V2
    again = _press(client, tasks)
    assert again["reused"] is False
    assert seen == [MINE, MINE_V2]


def test_a_failed_resume_read_does_not_score_against_someone_elses_cv(world):
    """If the caller's résumé cannot be read, the honest outcome is an error,
    not a number computed from the bundled owner's résumé."""
    client, db, seen, tasks = world
    db.fail_on[("user_resumes", "select")] = RuntimeError("connection reset")
    r = client.post("/api/score", json=BODY)
    assert r.status_code == 503, r.text
    assert "résumé" in r.json()["detail"]
    assert seen == []


def test_the_fresh_task_also_refuses_when_the_read_fails(world, monkeypatch):
    """The task re-reads (the résumé may change between request and task);
    the task must refuse too, not fall back."""
    import app as app_module
    from fastapi import HTTPException

    client, db, seen, _ = world
    db.fail_on[("user_resumes", "select")] = RuntimeError("connection reset")
    with pytest.raises(HTTPException) as e:
        app_module._score_fresh("user-1", app_module.ScoreRequest(**BODY))
    assert e.value.status_code == 503
    assert seen == []


def test_the_score_path_reads_through_the_one_validated_selector():
    """Structural companion to the behavioural tests above: the score path has
    no résumé read of its own, and never indexes the bundled résumés except as
    `_score_base_resume`'s labelled fallback."""
    import ast
    import pathlib

    src = pathlib.Path("app.py").read_text()
    funcs = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}

    def names(fn):
        return {getattr(c.func, "id", getattr(c.func, "attr", None))
                for c in ast.walk(funcs[fn]) if isinstance(c, ast.Call)}

    def subscripts_resumes(fn):
        return any(isinstance(n, ast.Subscript) and getattr(n.value, "id", None) == "_resumes"
                   for n in ast.walk(funcs[fn]))

    assert "fetch_tailorable_resume" in names("_score_base_resume")
    assert "_score_base_resume" in names("_score_fresh")
    assert "_score_base_resume" in names("score_job")
    assert not subscripts_resumes("_score_fresh"), "_score_fresh indexes the bundled résumés again"
