"""Layer A — programmatic floor checks for AI-generated apply answers.

Catches the obvious-bad cases before any human review:
- empty/truncated answers
- placeholder leakage ([fill me], TODO, etc.)
- echoed questions
- generic answers with zero personalization

Per Smart Apply Phase 1 spec §6.3 Layer A.
"""
from __future__ import annotations
import os

import pytest
from fastapi.testclient import TestClient

# Replace these IDs with real S/A-tier job IDs in your prod DB. They must
# have apply_url + resume_s3_key set so /api/apply/preview returns a full
# response. The 5 IDs are stored as an env var so this file can be re-used
# in different envs; for local dev set FLOOR_TEST_JOB_IDS to a comma-list.
FIXTURE_JOB_IDS = [j for j in os.environ.get("FLOOR_TEST_JOB_IDS", "").split(",") if j]

PLACEHOLDER_PATTERNS = ["[", "TODO", "FILL ME", "FIXME", "...", "Lorem ipsum"]


@pytest.fixture(scope="module")
def client():
    from app import app
    return TestClient(app)


def _profile_facts(profile: dict) -> list[str]:
    """Return short strings drawn from the profile that the AI should reference."""
    facts = []
    for skill in (profile.get("skills") or [])[:5]:
        if isinstance(skill, str) and len(skill) >= 3:
            facts.append(skill.lower())
    for role in (profile.get("target_roles") or [])[:5]:
        if isinstance(role, str):
            facts.append(role.lower())
    ctx = profile.get("candidate_context") or ""
    if ctx:
        # take the first significant phrase (4+ words)
        words = ctx.split()
        if len(words) >= 4:
            facts.append(" ".join(words[:4]).lower())
    return facts


@pytest.mark.skipif(
    not FIXTURE_JOB_IDS,
    reason="FLOOR_TEST_JOB_IDS not set",
)
@pytest.mark.parametrize("job_id", FIXTURE_JOB_IDS)
def test_answer_floor_per_job(client, job_id):
    """For each fixture job: every AI-generated answer passes the floor checks."""
    # Auth: floor test runs against staging with a fixture user — set a session
    # token via env var FLOOR_TEST_TOKEN.
    token = os.environ.get("FLOOR_TEST_TOKEN", "")
    if not token:
        pytest.skip("FLOOR_TEST_TOKEN not set")

    resp = client.get(
        f"/api/apply/preview/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"preview {job_id} returned {resp.status_code}"
    data = resp.json()

    questions = data.get("custom_questions", [])
    if not questions:
        pytest.skip(
            f"job {job_id} returned empty custom_questions — platform unsupported, skip floor checks"
        )

    profile = data.get("profile") or {}
    facts = _profile_facts(profile)

    fact_appearances = 0
    for q in questions:
        # Floor checks only apply to free-text AI answers. Bool answers
        # (yes_no, checkbox) and None (requires_user_action) are skipped.
        ai_answer = q.get("ai_answer")
        if not isinstance(ai_answer, str):
            continue
        answer = ai_answer.strip()
        question = (q.get("label") or "").strip()

        assert len(answer) >= 20, f"answer too short for {q['label']!r}: {answer!r}"
        assert answer.lower() != question.lower(), (
            f"answer echoes question: {q['label']!r}"
        )
        for pat in PLACEHOLDER_PATTERNS:
            assert pat.lower() not in answer.lower(), (
                f"placeholder {pat!r} in answer: {answer!r}"
            )

        if any(f in answer.lower() for f in facts):
            fact_appearances += 1

    assert fact_appearances >= 1, (
        f"job {job_id}: no profile fact ({facts!r}) appeared in any of {len(questions)} answers — "
        f"AI is producing generic responses with no personalization"
    )
