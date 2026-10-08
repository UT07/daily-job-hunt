"""Pressing Save & Score twice must give the same number twice.

"if I press save and score on a job multiple times the scores are different we
need to fix that. The scores need to be consistent otherwise it is AI slop"
— 2026-10-08.

The scores were not drifting because the model is random: both scoring paths
pass temperature=0. They drifted because a DIFFERENT MODEL answered each time.
The AI response cache is a SQLite file under /tmp, which ai_client.py forces it
to on Lambda, so it belongs to one execution environment and dies with it;
`_dead_providers` and the rate-limit cooldowns are in-memory on the same
object; and on a cache miss `complete_with_info` walks the provider list and
takes the first that answers. A container with Groq cooling down answers with
Cerebras, a fresh one answers with Groq, and Lambda runs as many containers as
it likes.

So the repeat case is made exact at the endpoint: the same job, scored against
the same resume, returns the score already on record. The tests that matter
most here are the ones pinning when reuse is REFUSED — a stale or mismatched
reuse is a worse failure than the drift it replaces.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs and incident response. " * 3)
BODY = {"job_description": JD, "job_title": "Site Reliability Engineer",
        "company": "Acme", "resume_type": "sre_devops"}


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret"}):
        yield


def _client(monkeypatch, stored_row, scorer=None):
    """A client whose `jobs` table answers with `stored_row`.

    `scorer` counts AI calls: the point of most of these tests is whether the
    model was consulted at all.
    """
    import app as app_module
    from auth import AuthUser, get_current_user

    chain = MagicMock()
    for m in ("select", "eq", "update", "insert", "maybe_single"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value = MagicMock(data=stored_row)
    db = MagicMock()
    db.client.table.return_value = chain
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "r.tex", "fullstack": "f.tex"})
    monkeypatch.setattr(app_module, "_posthog", None)
    monkeypatch.setattr(app_module, "_ai_client", MagicMock())

    calls = {"n": 0, "kwargs": None}

    def fake_score(job, resume_tex, **kw):
        # Counting calls is the point of most of these tests, and the kwargs
        # are recorded because num_calls/skip_cache are load-bearing: without
        # skip_cache, calls two and three return the cached first answer and
        # the median of three is the median of one.
        calls["n"] += 1
        calls["kwargs"] = kw
        return scorer() if scorer else {
            "match_score": 71, "ats_score": 70, "hiring_manager_score": 72,
            "tech_recruiter_score": 71, "reasoning": "fresh",
        }

    monkeypatch.setattr(app_module, "score_single_job_deterministic", fake_score)
    monkeypatch.setattr(app_module, "_find_or_create_job", lambda *a, **k: "job-1")

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.test")
    return TestClient(app_module.app, raise_server_exceptions=False), calls


SCORED = {
    "job_id": "job-1", "user_id": "user-1", "canonical_hash": "c-1",
    "match_score": 88, "ats_score": 86, "hiring_manager_score": 90,
    "tech_recruiter_score": 88, "match_reasoning": "strong platform overlap",
    "matched_resume": "sre_devops",
}


def test_the_same_job_returns_the_same_score_and_asks_no_model(monkeypatch):
    client, calls = _client(monkeypatch, SCORED)
    first = client.post("/api/score", json=BODY)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["avg_score"] == 88
    assert body["ats_score"] == 86
    assert body["reused"] is True
    assert calls["n"] == 0, "the AI was consulted for a job already scored"


def test_pressing_it_five_times_gives_one_answer(monkeypatch):
    # The actual complaint, as a test.
    client, calls = _client(monkeypatch, SCORED)
    seen = {client.post("/api/score", json=BODY).json()["avg_score"] for _ in range(5)}
    assert seen == {88}, f"five presses produced {seen}"
    assert calls["n"] == 0


def test_a_reused_score_says_it_was_reused(monkeypatch):
    # Without this the UI would imply it had re-evaluated the job.
    client, _ = _client(monkeypatch, SCORED)
    assert client.post("/api/score", json=BODY).json()["reused"] is True


# ---- when reuse must be REFUSED ----

def test_force_rescores_and_does_not_reuse(monkeypatch):
    client, calls = _client(monkeypatch, SCORED)
    r = client.post("/api/score", json={**BODY, "force": True})
    assert r.status_code == 200, r.text
    assert r.json()["avg_score"] == 71, "force returned the stored score"
    assert r.json()["reused"] is False
    assert calls["n"] == 1


def test_a_score_against_a_different_resume_is_not_reused(monkeypatch):
    # A score for the SRE resume does not answer "how does the full-stack
    # resume do against this job". Reusing it would be a wrong answer
    # delivered confidently, which is worse than the drift.
    client, calls = _client(monkeypatch, SCORED)
    r = client.post("/api/score", json={**BODY, "resume_type": "fullstack"})
    assert r.status_code == 200, r.text
    assert r.json()["reused"] is False
    assert calls["n"] == 1


def test_an_unscored_row_is_not_reused(monkeypatch):
    # The row exists (the job was added) but was never scored. match_score is
    # NULL, and 0 is a real score -- so the check must be `is None`, not falsy.
    client, calls = _client(monkeypatch, {**SCORED, "match_score": None})
    r = client.post("/api/score", json=BODY)
    assert r.json()["reused"] is False
    assert calls["n"] == 1


def test_a_genuine_zero_score_is_reused(monkeypatch):
    # The mirror of the above: 0 is a measured score, not a missing one.
    client, calls = _client(monkeypatch, {
        **SCORED, "match_score": 0, "ats_score": 0,
        "hiring_manager_score": 0, "tech_recruiter_score": 0})
    r = client.post("/api/score", json=BODY)
    assert r.json()["reused"] is True, "a real 0 was treated as unscored"
    assert calls["n"] == 0


def test_no_row_at_all_scores_normally(monkeypatch):
    client, calls = _client(monkeypatch, None)
    r = client.post("/api/score", json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["reused"] is False
    assert calls["n"] == 1


def test_a_failed_lookup_scores_rather_than_erroring(monkeypatch):
    # Reuse is an optimisation. If the read fails, score the job; refusing
    # would turn a transient DB blip into a broken button.
    import app as app_module
    from auth import AuthUser, get_current_user

    chain = MagicMock()
    for m in ("select", "eq", "maybe_single"):
        getattr(chain, m).return_value = chain
    chain.execute.side_effect = RuntimeError("connection reset")
    db = MagicMock()
    db.client.table.return_value = chain
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(app_module, "_resumes", {"sre_devops": "r.tex"})
    monkeypatch.setattr(app_module, "_posthog", None)
    monkeypatch.setattr(app_module, "_ai_client", MagicMock())
    monkeypatch.setattr(app_module, "score_single_job_deterministic",
                        lambda *a, **k: {
                            "match_score": 71, "ats_score": 70,
                            "hiring_manager_score": 72, "tech_recruiter_score": 71,
                            "reasoning": "fresh"})
    monkeypatch.setattr(app_module, "_find_or_create_job", lambda *a, **k: "job-1")
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.test")
    client = TestClient(app_module.app, raise_server_exceptions=False)
    r = client.post("/api/score", json=BODY)
    app_module.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json()["reused"] is False


def test_reuse_is_keyed_on_this_request_s_own_text(monkeypatch):
    """The lookup must use the hash of the JD in THIS request.

    The double answers with the same row whatever it is asked, so a hash built
    from anything else -- a stale variable, the wrong field -- would silently
    reuse the Acme score for an unrelated job. Asserting the hash that reaches
    `_stored_score` is what makes the reuse path safe rather than lucky.
    """
    import app as app_module
    from utils.canonical_hash import canonical_hash

    asked = []
    monkeypatch.setattr(app_module, "_stored_score",
                        lambda uid, ch, rt: asked.append((uid, ch, rt)) or None)
    client, calls = _client(monkeypatch, SCORED)
    monkeypatch.setattr(app_module, "_stored_score",
                        lambda uid, ch, rt: asked.append((uid, ch, rt)) or None)
    r = client.post("/api/score", json=BODY)

    assert r.status_code == 200, r.text
    assert len(asked) == 1
    user_id, chash, resume_type = asked[0]
    assert user_id == "user-1"
    assert resume_type == "sre_devops"
    assert chash == canonical_hash("Acme", "Site Reliability Engineer", JD)
    # _stored_score returned None, so the job was scored rather than reused.
    assert r.json()["reused"] is False
    assert calls["n"] == 1


def test_a_fresh_score_is_a_median_of_three_uncached_calls(monkeypatch):
    """The reason this endpoint stopped scoring through match_jobs.

    Measured 2026-10-08 against real providers, by a test whose skip had been
    `skipif(True)` since it was written so it had never once run: the same job,
    the same resume, the same prompt, temperature=0, and the same model
    answering returned ATS scores of **70, 85 and 75**. A fifteen-point spread
    crosses tier boundaries, so one sample decides A-tier versus B-tier by
    chance -- and reuse would then freeze that coin flip permanently.

    num_calls and skip_cache are asserted together deliberately. num_calls=3
    with the cache ON is the median of one wearing a disguise, which is the
    exact trap `score_single_job_deterministic`'s own docstring warns about.
    """
    client, calls = _client(monkeypatch, None)
    r = client.post("/api/score", json=BODY)
    assert r.status_code == 200, r.text
    assert calls["n"] == 1, "the deterministic scorer was not the engine used"
    assert calls["kwargs"].get("num_calls") == 3, calls["kwargs"]
    assert calls["kwargs"].get("skip_cache") is True, calls["kwargs"]


def test_the_scorer_is_given_this_request_s_own_job(monkeypatch):
    """score_single_job reads job['job_hash'] in its own error paths, so a
    missing key fails inside the scorer rather than here, where the message
    would make sense."""
    import app as app_module
    from utils.canonical_hash import canonical_hash

    captured = {}
    client, _ = _client(monkeypatch, None)
    monkeypatch.setattr(
        app_module, "score_single_job_deterministic",
        lambda job, tex, **kw: captured.update(job=job, tex=tex) or {
            "match_score": 71, "ats_score": 70, "hiring_manager_score": 72,
            "tech_recruiter_score": 71, "reasoning": "fresh"})
    r = client.post("/api/score", json=BODY)

    assert r.status_code == 200, r.text
    job = captured["job"]
    assert job["job_hash"] == canonical_hash("Acme", "Site Reliability Engineer", JD)
    assert job["title"] == "Site Reliability Engineer"
    assert job["company"] == "Acme"
    assert job["description"] == JD
    assert captured["tex"] == "r.tex", "scored against the wrong base resume"
