"""Every write to `jobs` must be scoped to the user who owns the row.

The jobs primary key is (job_id, user_id), and a manually added job gets
job_id = canonical_hash(company, title, description). So two users who paste
the same JD hold rows with the SAME job_id, and a write filtered only by
job_id lands on both: one user's résumé link, scores or resume_version end up
on the other's dashboard. Dormant while there is one user; cross-user PII the
day there are two.

These run against `postgrest_double.FakeSupabase`, which applies filters the
way PostgREST does. `test_postgrest_double.py` shows the same unscoped write
DOES clobber the second row there, so a pass here is evidence, not a mock
agreeing with itself.
"""
import ast
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase
from utils.canonical_hash import canonical_hash

ROOT = pathlib.Path(__file__).resolve().parents[2]

JD = "We need an engineer to run Kubernetes clusters and write Python services. " * 3
CHASH = canonical_hash("Acme", "SRE", JD)


def _row(user, **extra):
    base = {
        "job_id": CHASH, "user_id": user, "canonical_hash": CHASH, "job_hash": None,
        "title": "SRE", "company": "Acme", "description": JD, "source": "manual",
        "resume_s3_url": f"{user}.pdf", "match_score": 50, "resume_version": 1,
        "apply_url": f"https://{user}.example/apply",
    }
    base.update(extra)
    return base


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({
        "jobs": [_row("alice"), _row("bob")],
        "jobs_raw": [{"job_hash": CHASH}],
        "resume_versions": [],
    })
    monkeypatch.setattr(app_module, "_db", fake)
    return fake


def _bob(db):
    (row,) = db.rows("jobs", user_id="bob")
    return row


def test_artifact_update_leaves_the_other_users_row_alone(db):
    app_module._update_job_artifacts("alice", CHASH, {"resume_s3_url": "new.pdf"})
    assert db.rows("jobs", user_id="alice")[0]["resume_s3_url"] == "new.pdf"
    assert _bob(db)["resume_s3_url"] == "bob.pdf"


def test_artifact_update_without_a_user_writes_nothing(db):
    """No user means no scope; an unscoped write is the bug, not a fallback."""
    app_module._update_job_artifacts("", CHASH, {"resume_s3_url": "new.pdf"})
    assert {r["resume_s3_url"] for r in db.tables["jobs"]} == {"alice.pdf", "bob.pdf"}


def test_merge_into_an_existing_row_is_scoped(db):
    """Alice re-adding a JD merges into HER row (source -> manual). Bob's row
    with the same job_id must keep its own source."""
    _bob(db)["source"] = "scraped"
    job_id = app_module._find_or_create_job("alice", {
        "title": "SRE", "company": "Acme", "description": JD,
    })
    assert job_id == CHASH
    assert db.rows("jobs", user_id="alice")[0]["source"] == "manual"
    assert _bob(db)["source"] == "scraped"


def test_score_writes_only_the_callers_row(db, monkeypatch):
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "\\documentclass{x}"})
    monkeypatch.setattr(app_module, "score_single_job_deterministic", lambda *a, **k: {
        "match_score": 91, "ats_score": 90, "hiring_manager_score": 92,
        "tech_recruiter_score": 91, "reasoning": "fit",
    })
    req = app_module.ScoreRequest(job_description=JD, job_title="SRE", company="Acme", force=True)
    # The fresh path runs as a "score" task (_score_fresh) since the 72.6s/503
    # fix; its UPDATE is the write being scoped.
    out = app_module._score_fresh("alice", req)
    assert out["saved"] is True
    assert db.rows("jobs", user_id="alice")[0]["match_score"] == 91
    assert _bob(db)["match_score"] == 50


def test_regenerate_bumps_only_the_callers_resume_version(db, monkeypatch):
    sfn = MagicMock()
    sfn.start_execution.return_value = {"executionArn": "arn:aws:states:r:1:execution:sm:e1"}
    monkeypatch.setattr(app_module, "_get_sfn", lambda: sfn)
    monkeypatch.setattr(app_module, "_archive_job_artifacts", lambda job, v: {})
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(id="alice", email="a@x")
    try:
        with patch.dict(os.environ, {"SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:r:1:stateMachine:sm"}):
            r = TestClient(app_module.app).post(f"/api/pipeline/re-tailor/{CHASH}", json={})
    finally:
        app_module.app.dependency_overrides.clear()
    assert r.status_code == 202, r.text
    assert db.rows("jobs", user_id="alice")[0]["resume_version"] == 2
    assert _bob(db)["resume_version"] == 1


def test_studio_rebuild_writes_only_the_callers_row(db, monkeypatch):
    """_do_rebuild_sections ends in the artifact update; drive it end to end
    with S3 and the compiler stubbed so the write path is the real one."""
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"\\documentclass{x}\\begin{document}\\end{document}")}
    s3.generate_presigned_url.return_value = "https://signed/alice.pdf"
    monkeypatch.setattr(app_module, "_get_s3", lambda: s3)

    def _compile(path, out):
        p = pathlib.Path(out) / "r.pdf"
        p.write_bytes(b"%PDF")
        return str(p)

    monkeypatch.setattr(app_module, "compile_tex_to_pdf", _compile)
    monkeypatch.setattr(app_module, "_score_rebuilt_resume", lambda *a: None)
    import lambdas.pipeline.parse_sections as ps
    monkeypatch.setattr(ps, "rebuild_tex_from_sections", lambda s, b: b)

    app_module._do_rebuild_sections(CHASH, {}, "alice")
    assert db.rows("jobs", user_id="alice")[0]["resume_s3_url"] == "https://signed/alice.pdf"
    assert _bob(db)["resume_s3_url"] == "bob.pdf"


# ── structural: the next unscoped write fails CI, not review ─────────────────

def _chain(call):
    """(method, first-arg-literal) pairs along a fluent call chain."""
    out = []
    node = call
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        arg = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        out.append((node.func.attr, arg))
        node = node.func.value
    return out


def _unscoped_job_writes(path):
    tree = ast.parse(path.read_text())
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"):
            continue
        chain = _chain(node)
        methods = {m for m, _ in chain}
        if ("table", "jobs") not in chain:
            continue
        if methods & {"update", "delete", "upsert"} and ("eq", "user_id") not in chain:
            if "upsert" in methods:
                continue  # upsert_job sets user_id on the row and conflicts on (job_id,user_id)
            bad.append(node.lineno)
    return bad


@pytest.mark.parametrize("fname", ["app.py", "db_client.py"])
def test_every_jobs_update_or_delete_filters_on_user_id(fname):
    bad = _unscoped_job_writes(ROOT / fname)
    assert bad == [], f"{fname}: jobs writes not filtered by user_id at lines {bad}"


def test_the_structural_check_can_fail(tmp_path):
    """Known-kill control for the scanner itself."""
    f = tmp_path / "m.py"
    f.write_text('db.table("jobs").update({"a": 1}).eq("job_id", j).execute()\n'
                 'db.table("jobs").update({"a": 1}).eq("job_id", j).eq("user_id", u).execute()\n')
    assert _unscoped_job_writes(f) == [1]
