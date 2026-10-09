"""The shared jobs_raw row carries no submitter's apply_url or location.

Second half of the 2026-10-09 security review ("cross-tenant data tampering
in app.py"), which re-flagged the insert-if-absent fix as incomplete, rightly.
`ignore_duplicates` made jobs_raw first-writer-wins, but the manual write
still stored apply_url and location, which `canonical_hash` does not cover
(company, title, description only). score_batch reads jobs_raw with
`select("*")` and copied apply_url and location into the SCORING user's own
jobs row. So whoever submitted a JD first chose, for every later user who
added the same JD, the Apply link (phishing) and the location (which drives
the geo / work-auth score cap).

The complete fix, end to end:
  * a manual jobs_raw row stores only hash-bound content: job_hash, title,
    company, description, source='manual';
  * the submitter's own apply_url and location go to THEIR `jobs` row only --
    Save & Score through `_find_or_create_job` as before, and run-single now
    the same way before it starts the execution (the state machine's Pass
    states are untouched: naming a new field there makes it mandatory for
    every caller, which cost two outages, see test_single_job_sfn_contract);
  * score_batch prefers the user's own row for those two fields, for the row
    it writes AND for the geo cap, and never blanks a stored value with an
    empty one from jobs_raw.

Scraped jobs are unchanged: their jobs_raw row is the scraper's, and its
apply_url still reaches the user.

Everything runs over the PostgREST double (filters, or_, upsert resolutions
pinned in test_postgrest_double.py); the scorer is patched and no model is
called.
"""
import copy
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.unit.postgrest_double import FakeSupabase
from utils.canonical_hash import canonical_hash

sys.path.insert(0, "lambdas/pipeline")
import save_job  # noqa: E402
import score_batch  # noqa: E402

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs, on-call rotation and incident response for a global "
      "payments product. ") * 3
BASE = {"job_description": JD, "job_title": "Site Reliability Engineer", "company": "Acme",
        "resume_type": "sre_devops"}
EVIL = {**BASE, "location": "Lagos, Nigeria", "apply_url": "https://attacker.example/phish"}
GOOD = {**BASE, "location": "New York, USA",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/4242"}
HASH = canonical_hash("Acme", "Site Reliability Engineer", JD)
RESUME = "\\documentclass{article}\\begin{document}SRE, Kubernetes, on-call\\end{document}"
ROUTES = ["/api/score", "/api/pipeline/run-single"]


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret",
                                 "SINGLE_JOB_PIPELINE_ARN": "arn:aws:states:x:1:stateMachine:s"}):
        yield


class _Sfn:
    def __init__(self):
        self.inputs = []

    def start_execution(self, stateMachineArn, input, **_):
        import datetime
        import json
        self.inputs.append(json.loads(input))
        return {"executionArn": "arn:aws:states:x:1:execution:s:e-1",
                "startDate": datetime.datetime(2026, 10, 9)}


@pytest.fixture
def world(monkeypatch, inline_tasks):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = FakeSupabase({"jobs": [], "jobs_raw": [], "pipeline_tasks": [],
                       "users": [{"id": u, "email": f"{u}@x.test", "work_authorizations": {}}
                                 for u in ("attacker", "victim")],
                       "user_resumes": [{"user_id": u, "resume_key": "default",
                                         "tex_content": RESUME, "created_at": "2026-10-01"}
                                        for u in ("attacker", "victim")]})
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": RESUME})
    monkeypatch.setattr(app_module, "_posthog", None)
    scored = {"match_score": 95, "ats_score": 95, "hiring_manager_score": 95,
              "tech_recruiter_score": 95, "reasoning": "fits", "gaps": [],
              "key_matches": ["Kubernetes: 5 years, cut MTTR 40%"]}
    monkeypatch.setattr(app_module, "score_single_job_deterministic",
                        lambda *a, **k: dict(scored))
    monkeypatch.setattr(score_batch, "score_single_job_deterministic",
                        lambda *a, **k: copy.deepcopy(scored))
    monkeypatch.setattr(score_batch, "get_supabase", lambda: db)
    monkeypatch.setattr(save_job, "get_supabase", lambda: db)
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://s3.example/presigned"
    monkeypatch.setattr(save_job.boto3, "client", lambda *_a, **_k: s3)
    sfn = _Sfn()
    monkeypatch.setattr(app_module, "_get_sfn", lambda: sfn)
    who = {"id": "attacker"}
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=who["id"], email=f"{who['id']}@x.test")
    yield TestClient(app_module.app, raise_server_exceptions=False), db, who, sfn
    app_module.app.dependency_overrides.clear()


def _submit(client, route, body):
    r = client.post(route, json=body)
    assert r.status_code == 202, r.text


def _pipeline(user_id):
    """What the single-job state machine runs after run-single: score, then save."""
    score_batch.handler({"user_id": user_id, "new_job_hashes": [HASH],
                         "min_match_score": 0}, None)
    save_job.handler({"job_hash": HASH, "user_id": user_id,
                      "compile_result": {"pdf_s3_key": f"users/{user_id}/r.pdf"}}, None)


def _rows(db, user):
    return db.rows("jobs", user_id=user)


# (b) ------------------------------------------------------------------------

@pytest.mark.parametrize("route", ROUTES)
def test_the_shared_row_never_holds_a_submitters_apply_url_or_location(world, route):
    client, db, _, _ = world
    _submit(client, route, GOOD)
    raw = db.rows("jobs_raw", job_hash=HASH)
    assert len(raw) == 1
    assert not raw[0].get("apply_url"), raw[0]
    assert not raw[0].get("location"), raw[0]
    assert raw[0]["description"] == JD and raw[0]["source"] == "manual"


# (a) ------------------------------------------------------------------------

@pytest.mark.parametrize("route", ROUTES)
def test_the_attacker_first_cannot_choose_the_victims_apply_link(world, route):
    client, db, who, _ = world
    _submit(client, route, EVIL)
    _pipeline("attacker")

    who["id"] = "victim"
    _submit(client, route, GOOD)
    _pipeline("victim")

    rows = _rows(db, "victim")
    assert len(rows) == 1, f"victim has {len(rows)} rows for one job"
    row = rows[0]
    assert row["apply_url"] == GOOD["apply_url"]
    assert row["location"] == GOOD["location"]
    assert "attacker.example" not in repr(row) and "Lagos" not in repr(row), row


# (c) ------------------------------------------------------------------------

def test_the_users_own_apply_url_and_location_survive_run_single_end_to_end(world):
    client, db, who, sfn = world
    who["id"] = "victim"
    _submit(client, "/api/pipeline/run-single", GOOD)
    assert sfn.inputs[-1]["job_hash"] == HASH
    _pipeline("victim")

    [row] = _rows(db, "victim")
    assert row["apply_url"] == GOOD["apply_url"]
    assert row["location"] == GOOD["location"]
    assert row["apply_platform"] == "greenhouse", "platform not derived from the user's URL"
    # The user's location reached the geo cap: a US job is capped below S.
    assert row["match_score"] <= 89, row["match_score"]
    assert row["resume_s3_key"] == "users/victim/r.pdf", "save_job missed the row"


def test_run_single_on_a_job_the_user_already_has_creates_no_second_row(world):
    """run-single now writes the user's row before the execution. A user whose
    row came from the scrapers holds the hash in job_hash, not canonical_hash;
    a canonical_hash-only lookup would insert a duplicate of it."""
    client, db, who, _ = world
    who["id"] = "victim"
    db.tables["jobs_raw"].append(copy.deepcopy(SCRAPED))
    db.tables["jobs"].append({"job_id": "scraped-1", "user_id": "victim", "job_hash": HASH,
                              "canonical_hash": None, "title": "Site Reliability Engineer",
                              "company": "Acme", "description": JD,
                              "apply_url": "", "location": ""})
    _submit(client, "/api/pipeline/run-single", GOOD)
    _pipeline("victim")
    [row] = _rows(db, "victim")
    assert row["job_id"] == "scraped-1"
    assert row["apply_url"] == GOOD["apply_url"]


# (d) ------------------------------------------------------------------------

SCRAPED = {"job_hash": HASH, "title": "Site Reliability Engineer", "company": "Acme",
           "description": JD, "location": "Dublin, Ireland",
           "apply_url": "https://jobs.lever.co/acme/abc-123", "source": "linkedin"}


def test_a_scraped_row_keeps_its_scraped_apply_url(world):
    _, db, _, _ = world
    db.tables["jobs_raw"].append(copy.deepcopy(SCRAPED))
    _pipeline("victim")
    [row] = _rows(db, "victim")
    assert row["apply_url"] == SCRAPED["apply_url"]
    assert row["location"] == SCRAPED["location"]
    assert db.rows("jobs_raw", job_hash=HASH) == [SCRAPED]


def test_the_users_own_values_beat_a_scraped_rows(world):
    """B adds a JD the scrapers also stored, with B's own link and location.
    B's row, B's platform and B's geo cap all follow B's values."""
    client, db, who, _ = world
    who["id"] = "victim"
    db.tables["jobs_raw"].append(copy.deepcopy(SCRAPED))
    _submit(client, "/api/pipeline/run-single", GOOD)
    _pipeline("victim")
    [row] = _rows(db, "victim")
    assert row["apply_url"] == GOOD["apply_url"]
    assert row["location"] == GOOD["location"]
    assert row["apply_platform"] == "greenhouse", row["apply_platform"]
    assert row["match_score"] <= 89, "the cap judged the scraper's Dublin, not the user's US"


@pytest.mark.parametrize("incoming", ["", "https://other.example/apply"])
def test_write_job_row_never_replaces_or_blanks_a_stored_value(incoming):
    """Direct, because the handler's preference for the user's row normally
    hides this: _write_job_row is also reached by the missing-column retry,
    which looks the row up itself."""
    db = FakeSupabase({"jobs": [{"job_id": "j-1", "user_id": "u", "job_hash": HASH,
                                 "apply_url": "https://mine.example", "location": "Cork"}]})
    score_batch._write_job_row(db, {"job_id": "new", "user_id": "u", "job_hash": HASH,
                                    "apply_url": incoming, "location": incoming,
                                    "match_score": 77})
    [row] = db.rows("jobs", user_id="u")
    assert row["apply_url"] == "https://mine.example"
    assert row["location"] == "Cork"
    assert row["match_score"] == 77


def test_write_job_row_fills_an_empty_stored_value():
    db = FakeSupabase({"jobs": [{"job_id": "j-1", "user_id": "u", "job_hash": HASH,
                                 "apply_url": "", "location": None}]})
    score_batch._write_job_row(db, {"job_id": "new", "user_id": "u", "job_hash": HASH,
                                    "apply_url": "https://scraped.example", "location": "Dublin"})
    [row] = db.rows("jobs", user_id="u")
    assert row["apply_url"] == "https://scraped.example"
    assert row["location"] == "Dublin"


def test_a_rescore_never_blanks_a_stored_apply_url_or_location(world):
    _, db, _, _ = world
    db.tables["jobs_raw"].append({**SCRAPED, "apply_url": "", "location": ""})
    db.tables["jobs"].append({"job_id": "j-1", "user_id": "victim", "job_hash": HASH,
                              "apply_url": "https://mine.example/apply",
                              "location": "Cork, Ireland"})
    _pipeline("victim")
    [row] = _rows(db, "victim")
    assert row["apply_url"] == "https://mine.example/apply"
    assert row["location"] == "Cork, Ireland"
    assert row["match_score"] == 95, "the update did not land on the existing row"
