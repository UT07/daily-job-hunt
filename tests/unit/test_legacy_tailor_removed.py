"""POST /api/tailor is gone; what remains of its worker writes per-user keys.

/api/tailor had three defects (audit 2026-10-08): its S3 key
`web/{date}/resumes/{company}_{title}_resume.pdf` was not namespaced by user,
so two users tailoring for the same company/title on the same day overwrote
each other's PDF; it stored only the 7-day presigned URL and never
resume_s3_key (CLAUDE.md #9); and it tailored the repo-bundled owner résumé
whoever called it. Nothing in web/src calls it -- Add Job goes through
/api/pipeline/run-single -- so it was removed rather than repaired.

The "tailor" task type survives for re_tailor_job's local-dev fallback (no
SINGLE_JOB_PIPELINE_ARN), and /api/cover-letter still uses the sibling worker,
so both workers' keys are namespaced under users/{user_id}/ here too.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module


def test_the_route_is_gone():
    paths = {(getattr(r, "path", None), m) for r in app_module.app.routes
             for m in (getattr(r, "methods", None) or ())}
    assert ("/api/tailor", "POST") not in paths


def test_nothing_in_the_web_client_calls_it():
    web = Path(__file__).resolve().parents[2] / "web" / "src"
    hits = [p for p in web.rglob("*.js*") if "/api/tailor'" in p.read_text()
            or '/api/tailor"' in p.read_text() or "/api/tailor`" in p.read_text()]
    assert hits == []


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    uploaded = []

    def _tailor(job, base, ai, out):
        p = Path(out) / "t.tex"
        p.write_text("\\documentclass{x}")
        return str(p)

    def _compile(path, out):
        p = Path(out) / "o.pdf"
        p.write_bytes(b"%PDF")
        return str(p)

    monkeypatch.setattr(app_module, "tailor_resume", _tailor)
    monkeypatch.setattr(app_module, "generate_cover_letter", _tailor)
    monkeypatch.setattr(app_module, "score_and_improve", lambda tex, job, ai: (tex, {}))
    monkeypatch.setattr(app_module, "compile_tex_to_pdf", _compile)
    monkeypatch.setattr(app_module, "s3_upload_file",
                        lambda path, key, bucket: uploaded.append(key) or f"https://signed/{key}?X-Amz-Signature=s")
    return uploaded


JOB = SimpleNamespace(title="SRE", company="Acme", description="jd", location="")


def test_tailor_worker_key_is_per_user_and_returned(stub_pipeline):
    out = app_module._do_tailor(JOB, "base", "sre_devops", "Acme", "SRE", "alice")
    (key,) = stub_pipeline
    assert key.startswith("users/alice/")
    assert out["s3_key"] == key


def test_two_users_same_company_title_day_do_not_share_a_key(stub_pipeline):
    app_module._do_tailor(JOB, "base", "sre_devops", "Acme", "SRE", "alice")
    app_module._do_tailor(JOB, "base", "sre_devops", "Acme", "SRE", "bob")
    assert len(set(stub_pipeline)) == 2


def test_cover_letter_worker_key_is_per_user(stub_pipeline):
    app_module._do_cover_letter(JOB, "base", "Acme", "SRE", "alice")
    app_module._do_cover_letter(JOB, "base", "Acme", "SRE", "bob")
    assert [k.split("/")[1] for k in stub_pipeline] == ["alice", "bob"]


def test_the_tailor_task_stores_the_key_not_only_the_url(stub_pipeline, monkeypatch):
    written = {}
    monkeypatch.setattr(app_module, "_update_job_artifacts",
                        lambda uid, jid, updates: written.update(updates))
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "base"})
    app_module._dispatch_task("tailor", {"job_id": "j1", "company": "Acme", "job_title": "SRE",
                                         "job_description": "jd", "resume_type": "sre_devops"},
                              user_id="alice")
    assert written["resume_s3_key"].startswith("users/alice/")
    assert written["resume_s3_url"].startswith("https://signed/users/alice/")
