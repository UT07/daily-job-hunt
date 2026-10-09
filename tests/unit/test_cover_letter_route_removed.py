"""POST /api/cover-letter is gone, and so is the worker only it reached.

It generated a cover letter from the repo-bundled owner résumé (`_resumes`,
loaded from the repo at cold start) for whoever called it, so any user got a
letter written from someone else's career. Nothing in web/src called it: the
"Cover Letter" button on Add Job posts to /api/pipeline/run-single, whose
GenerateCoverLetter state reads the caller's own résumé. Removed 2026-10-08
rather than repaired.

The "cover_letter" SQS task type was only ever enqueued by that route, so it
goes too. The "tailor" task type stays: re_tailor_job's local-dev fallback
(no SINGLE_JOB_PIPELINE_ARN) still enqueues it from a live route.
"""
from pathlib import Path

import pytest

import app as app_module


def _routes():
    return {(getattr(r, "path", None), m) for r in app_module.app.routes
            for m in (getattr(r, "methods", None) or ())}


def test_the_route_is_gone():
    assert ("/api/cover-letter", "POST") not in _routes()


def test_its_request_model_is_gone():
    assert not hasattr(app_module, "CoverLetterRequest")


def test_nothing_in_the_web_client_calls_it():
    web = Path(__file__).resolve().parents[2] / "web" / "src"
    hits = [p for p in web.rglob("*.js*") if "/api/cover-letter" in p.read_text()]
    assert hits == []


def test_the_worker_only_it_reached_is_gone():
    assert not hasattr(app_module, "_do_cover_letter")
    with pytest.raises(ValueError, match="Unknown task_type"):
        app_module._dispatch_task("cover_letter", {"job_id": "j1"}, user_id="alice")


def test_the_tailor_task_a_live_route_still_enqueues_survives():
    # Population check for the removal: re-tailor is live and its local-dev
    # branch enqueues "tailor", so that worker must not have gone with it.
    assert ("/api/pipeline/re-tailor/{job_id}", "POST") in _routes()
    assert callable(getattr(app_module, "_do_tailor", None))
