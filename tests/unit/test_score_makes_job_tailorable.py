"""A job created by Save & Score must be tailorable straight away.

`/api/score` creates a `jobs` row through `_find_or_create_job`, keyed by
`canonical_hash`, and never wrote `jobs_raw`. Every generator reads the posting
text from `jobs_raw` by that hash, so Generate Resume / Regenerate / Cover
Letter on a Save-&-Score'd job answered 409 "The original posting text for this
job was never stored" -- for a job whose text the user had just pasted.

`/api/pipeline/run-single` already upserted `jobs_raw` under `canonical_hash`.
The score path now goes through the SAME function (`_upsert_jobs_raw`), not a
second copy of it (CLAUDE.md #10).

These tests run the real endpoints end to end over the PostgREST double, which
applies filters, so "the re-tailor path finds the row" means the row it looks
up by `job_hash` really exists with that key. No model is called: the scorer is
patched, and the state machine is a stub that records its input.
"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs and incident response. " * 3)
BODY = {"job_description": JD, "job_title": "Site Reliability Engineer",
        "company": "Acme", "location": "Dublin", "resume_type": "sre_devops",
        "apply_url": "https://acme.example/jobs/1"}


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret",
                                 "SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:x:1:stateMachine:s"}):
        yield


class _Sfn:
    def __init__(self):
        self.inputs = []

    def start_execution(self, stateMachineArn, input, **_):
        import json
        self.inputs.append(json.loads(input))
        return {"executionArn": "arn:aws:states:x:1:execution:s:e-1"}


@pytest.fixture
def world(monkeypatch, inline_tasks):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = FakeSupabase({"jobs": [], "jobs_raw": [], "user_resumes": [], "users": []})
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "bundled.tex"})
    monkeypatch.setattr(app_module, "_posthog", None)

    def fake_score(job, resume_tex, **kw):
        return {"match_score": 82, "ats_score": 80, "hiring_manager_score": 84,
                "tech_recruiter_score": 82, "reasoning": "fits"}

    monkeypatch.setattr(app_module, "score_single_job_deterministic", fake_score)
    sfn = _Sfn()
    monkeypatch.setattr(app_module, "_get_sfn", lambda: sfn)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.test")
    yield TestClient(app_module.app, raise_server_exceptions=False), db, sfn, inline_tasks
    app_module.app.dependency_overrides.clear()


def _score(client, tasks):
    r = client.post("/api/score", json=BODY)
    assert r.status_code == 202, r.text
    out = tasks[r.json()["task_id"]]["result"]
    assert out["saved"] is True, out
    return out


def test_a_freshly_scored_job_can_be_regenerated(world):
    client, db, sfn, tasks = world
    out = _score(client, tasks)

    r = client.post(f"/api/pipeline/re-tailor/{out['job_id']}", json={"scope": "resume"})
    assert r.status_code == 202, (
        f"re-tailor of a Save-&-Score'd job answered {r.status_code}: {r.text[:300]}")
    started_with = sfn.inputs[-1]["job_hash"]
    assert db.rows("jobs_raw", job_hash=started_with), (
        "the state machine was started with a hash jobs_raw does not hold")


def test_the_jobs_raw_row_carries_the_posting(world):
    client, db, _, tasks = world
    out = _score(client, tasks)
    job = db.rows("jobs", job_id=out["job_id"], user_id="user-1")[0]
    raw = db.rows("jobs_raw", job_hash=job["canonical_hash"])
    assert len(raw) == 1
    assert raw[0]["description"] == JD
    assert raw[0]["title"] == BODY["job_title"]
    assert raw[0]["company"] == "Acme"
    assert raw[0]["location"] == "Dublin"
    assert raw[0]["apply_url"] == BODY["apply_url"]
    assert raw[0]["source"] == "manual"


def test_a_reused_score_also_repairs_an_older_row(world):
    """Rows saved before this fix have a score and no jobs_raw. Pressing Save &
    Score again reuses the score (no model call) -- and must still store the
    posting, or that job stays untailorable forever."""
    client, db, _, tasks = world
    out = _score(client, tasks)
    db.tables["jobs_raw"] = []  # the pre-fix state

    again = client.post("/api/score", json=BODY)
    assert again.status_code == 200 and again.json()["reused"] is True, again.text
    job = db.rows("jobs", job_id=out["job_id"], user_id="user-1")[0]
    assert db.rows("jobs_raw", job_hash=job["canonical_hash"])


def test_score_and_run_single_write_jobs_raw_through_one_function():
    """One writer, so the two paths cannot drift on keys or columns."""
    import ast
    import pathlib

    src = pathlib.Path("app.py").read_text()
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def calls(fn, name):
        return any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == name
                   for c in ast.walk(funcs[fn]))

    def touches_jobs_raw(fn):
        return any(isinstance(c, ast.Constant) and c.value == "jobs_raw"
                   for p in ast.walk(funcs[fn]) if isinstance(p, ast.Call)
                   for c in p.args)

    assert calls("run_single_job", "_upsert_jobs_raw")
    assert calls("score_job", "_upsert_jobs_raw")
    assert not touches_jobs_raw("run_single_job"), "run-single kept its own jobs_raw write"
    assert not touches_jobs_raw("score_job")
