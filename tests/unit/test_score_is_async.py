"""A fresh Save & Score must not make the browser wait on the model.

Measured in production CloudWatch logs: a fresh POST /api/score took 72.6s.
Since 2026-10-08 it scores as the median of three sequential LLM calls
(`score_single_job_deterministic(num_calls=3, skip_cache=True)`), and API
Gateway's integration timeout is ~30s -- so the user got a 503 while the Lambda
carried on, returned 200 to nobody, and saved the row. The button reported
failure for work that had succeeded.

The fresh path is now a task: 202 + poll_url, scored by `_score_fresh` via
`_dispatch_task("score", ...)` on the SQS worker, polled at /api/tasks/{id}.
The reuse path (a stored score, no AI call) stays a synchronous 200.

What is pinned here, each against the way it would actually break:
  (a) the request answers exactly 202 with a poll URL and asks NO model;
  (b) the "score" task goes to `_score_fresh` without the generic
      `_find_or_create_job` call at the top of `_dispatch_task` running first;
  (c) the result a poller receives, after the real SQS worker ran, has
      ScoreResponse's fields -- the same shape the frontend fixture renders;
  (d) structurally, the route function itself never calls the scorer.
"""
import ast
import inspect
import json
import os
import pathlib
import textwrap
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import AuthUser, get_current_user
from tests.unit.postgrest_double import FakeSupabase

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs and incident response. " * 3)
BODY = {"job_description": JD, "job_title": "Site Reliability Engineer",
        "company": "Acme", "resume_type": "sre_devops"}
SCORED = {"match_score": 71, "ats_score": 70, "hiring_manager_score": 72,
          "tech_recruiter_score": 71, "reasoning": "fresh"}
FIXTURE = (pathlib.Path(__file__).resolve().parents[2]
           / "web" / "src" / "test" / "fixtures" / "score_task_result.json")


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({"jobs": [], "pipeline_tasks": []})
    monkeypatch.setattr(app_module, "_db", fake)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "r.tex"})
    monkeypatch.setattr(app_module, "_posthog", None)
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="alice", email="a@x")
    yield fake
    app_module.app.dependency_overrides.clear()


def _scorer_spy(monkeypatch):
    calls = []

    def spy(job, tex, **kw):
        calls.append(kw)
        return dict(SCORED)

    monkeypatch.setattr(app_module, "score_single_job_deterministic", spy)
    return calls


# ── (a) the request itself ──────────────────────────────────────────────────

def test_a_fresh_score_answers_202_and_asks_no_model_in_the_request(db, monkeypatch):
    scorer = _scorer_spy(monkeypatch)
    enqueued = []
    monkeypatch.setattr(app_module, "_enqueue_task",
                        lambda *a: enqueued.append(a))

    r = TestClient(app_module.app).post("/api/score", json=BODY)

    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == {"task_id", "poll_url"}
    assert body["poll_url"] == f"/api/tasks/{body['task_id']}"
    assert scorer == [], "the model was called inside the HTTP request"
    (task_id, user_id, task_type, payload), = enqueued
    assert (task_id, user_id, task_type) == (body["task_id"], "alice", "score")
    # The worker rebuilds a ScoreRequest from this, so it must be one.
    assert app_module.ScoreRequest(**payload).job_description == JD


def test_a_stored_score_is_still_a_synchronous_200(db, monkeypatch):
    """Reuse makes no AI call and is instant, so it must not become a task."""
    from utils.canonical_hash import canonical_hash
    chash = canonical_hash("Acme", "Site Reliability Engineer", JD)
    db.tables["jobs"].append({
        "job_id": chash, "user_id": "alice", "canonical_hash": chash,
        "match_score": 88, "ats_score": 86, "hiring_manager_score": 90,
        "tech_recruiter_score": 88, "match_reasoning": "stored",
        "matched_resume": "sre_devops"})
    scorer = _scorer_spy(monkeypatch)
    monkeypatch.setattr(app_module, "_enqueue_task",
                        lambda *a: pytest.fail("a stored score was enqueued"))

    r = TestClient(app_module.app).post("/api/score", json=BODY)

    assert r.status_code == 200, r.text
    assert r.json()["reused"] is True and r.json()["avg_score"] == 88
    assert scorer == []


# ── (b) dispatch ────────────────────────────────────────────────────────────

def test_dispatch_routes_score_to_score_fresh_and_skips_the_generic_create(monkeypatch):
    order = []
    monkeypatch.setattr(app_module, "_find_or_create_job",
                        lambda uid, p: order.append(("generic_create", p)) or "x")
    sentinel = {"from": "_score_fresh"}

    def fake_fresh(user_id, req):
        order.append(("score_fresh", user_id, req))
        return sentinel

    monkeypatch.setattr(app_module, "_score_fresh", fake_fresh)
    payload = app_module.ScoreRequest(**BODY).model_dump()

    out = app_module._dispatch_task("score", payload, user_id="alice")

    assert out is sentinel
    assert [o[0] for o in order] == ["score_fresh"], order
    _, user_id, req = order[0]
    assert user_id == "alice"
    assert isinstance(req, app_module.ScoreRequest) and req.company == "Acme"


def test_a_score_task_creates_exactly_one_correctly_keyed_row(db, monkeypatch):
    """Through the real chain (JSON-round-tripped payload, real dispatch, real
    _score_fresh), the score lands on exactly one row keyed by this job's
    canonical hash. Note what this does NOT catch: because
    _find_or_create_job reads both key spellings, a dispatch that ran the
    generic create first would still find the same row. The spy test above is
    what pins the order (measured: it alone kills that mutant)."""
    from utils.canonical_hash import canonical_hash
    _scorer_spy(monkeypatch)
    payload = json.loads(json.dumps(app_module.ScoreRequest(**BODY).model_dump()))

    out = app_module._dispatch_task("score", payload, user_id="alice")

    (row,) = db.rows("jobs", user_id="alice")
    assert row["job_id"] == canonical_hash("Acme", "Site Reliability Engineer", JD)
    assert out["job_id"] == row["job_id"] and out["saved"] is True


# ── (c) what the poller receives ────────────────────────────────────────────

def test_the_polled_result_is_a_score_response_after_the_real_sqs_worker(db, monkeypatch):
    """POST -> SQS message -> _process_sqs_task -> pipeline_tasks -> GET.

    Only SQS itself is doubled. The payload round-trips through the task row
    the way it does in production, and the result is read back through the
    same GET /api/tasks/{id} the browser polls.
    """
    _scorer_spy(monkeypatch)
    sent = []

    class FakeSQS:
        def send_message(self, QueueUrl, MessageBody):
            sent.append(MessageBody)

    monkeypatch.setattr(app_module, "TASK_QUEUE_URL", "https://sqs.test/q")
    monkeypatch.setattr(app_module.boto3, "client", lambda name, **k: FakeSQS())
    monkeypatch.setattr(app_module, "_initialize_state", lambda: None)
    client = TestClient(app_module.app)

    r = client.post("/api/score", json=BODY)
    assert r.status_code == 202, r.text
    poll_url = r.json()["poll_url"]
    assert client.get(poll_url).json() == {"status": "running"}

    (message,) = sent
    out = app_module._process_sqs_task(
        {"Records": [{"messageId": "m1", "body": message}]}, None)
    assert out == {"batchItemFailures": []}

    task = client.get(poll_url).json()
    assert task["status"] == "done", task
    result = task["result"]
    assert set(result) == set(app_module.ScoreResponse.model_fields)
    assert app_module.ScoreResponse(**result).model_dump() == result
    assert result["avg_score"] == 71 and result["saved"] is True
    assert result["reused"] is False
    # The frontend test renders this fixture as the polled result, so it must
    # have exactly the shape the worker produces.
    fixture = json.loads(FIXTURE.read_text())
    assert set(fixture) == set(result)
    for k in result:
        assert type(fixture[k]) is type(result[k]), k


def test_the_frontend_fixture_is_a_valid_score_response():
    fixture = json.loads(FIXTURE.read_text())
    assert set(fixture) == set(app_module.ScoreResponse.model_fields)
    assert app_module.ScoreResponse(**fixture).model_dump() == fixture


# ── (d) structural ──────────────────────────────────────────────────────────

_SLOW = {"score_single_job_deterministic", "_score_fresh", "_dispatch_task",
         "score_single_job", "match_jobs"}


def _called_names(fn) -> set:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names


def test_the_route_function_never_calls_the_scorer_synchronously():
    called = _called_names(app_module.score_job)
    assert "_enqueue_task" in called, "the route no longer enqueues"
    assert not (called & _SLOW), (
        f"score_job calls {sorted(called & _SLOW)} inside the HTTP request -- "
        "a fresh score is three sequential LLM calls (72.6s measured) and API "
        "Gateway gives up at ~30s")


def test_the_structural_check_can_see_a_synchronous_call():
    """Control: the AST walk must detect the pattern it exists to forbid."""
    def regressed(req, user):
        return _score_fresh(user.id, req)  # noqa: F821

    assert "_score_fresh" in _called_names(regressed)


def test_the_route_declares_no_response_model_that_would_reject_the_202():
    route = next(r for r in app_module.app.routes
                 if getattr(r, "path", "") == "/api/score")
    assert route.response_model is None


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield
