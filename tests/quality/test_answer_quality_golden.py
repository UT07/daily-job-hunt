"""Layer B — golden fixture comparison.

For each fixture job, fetch /api/apply/preview and compare each AI answer to
its hand-written ideal via cosine similarity over TF-IDF vectors.

Thresholds (per Smart Apply Phase 1 spec §6.3 Layer B):
- similarity >0.6 → pass
- 0.4-0.6     → emit a warning but don't fail (so prompt iteration isn't blocked)
- <0.4        → fail (prompt regression)
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_apply_answers.json"


def _cosine_similarity(a: str, b: str) -> float:
    """TF-IDF cosine similarity. Lazy-import sklearn so tests skip cleanly
    when it's not installed."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        pytest.skip("scikit-learn not installed — pip install scikit-learn")

    if not a.strip() or not b.strip():
        return 0.0
    v = TfidfVectorizer().fit_transform([a, b])
    return float(cosine_similarity(v[0:1], v[1:2])[0, 0])


def _load_fixtures():
    if not FIXTURE_PATH.exists():
        return []
    data = json.loads(FIXTURE_PATH.read_text())
    return [f for f in data["fixtures"] if f["ideal_answers"]]  # only filled ones


@pytest.fixture(scope="module")
def client():
    from app import app
    return TestClient(app)


@pytest.mark.parametrize(
    "fixture", _load_fixtures(), ids=lambda f: f["job_id"]
)
def test_golden_answer_similarity(client, fixture):
    token = os.environ.get("FLOOR_TEST_TOKEN", "")
    if not token:
        pytest.skip("FLOOR_TEST_TOKEN not set")

    resp = client.get(
        f"/api/apply/preview/{fixture['job_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    ai_answers = {q["question"]: q.get("answer", "") for q in data.get("custom_questions", [])}

    failures = []
    warnings = []
    for question, ideal in fixture["ideal_answers"].items():
        ai = ai_answers.get(question, "")
        if not ai:
            failures.append(f"AI did not produce an answer for {question!r}")
            continue
        sim = _cosine_similarity(ai, ideal)
        if sim < 0.4:
            failures.append(
                f"{question!r}: similarity {sim:.2f} < 0.4 (regression)\n"
                f"  ai:    {ai}\n  ideal: {ideal}"
            )
        elif sim < 0.6:
            warnings.append(f"{question!r}: similarity {sim:.2f} (warning)")

    if warnings:
        print("\nWARNINGS:\n" + "\n".join(warnings))
    assert not failures, "\n".join(failures)
