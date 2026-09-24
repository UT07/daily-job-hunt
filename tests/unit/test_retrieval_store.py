import json
import math
from unittest.mock import MagicMock, patch

from retrieval import store


def test_cosine_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert math.isclose(store.cosine(v, v), 1.0, rel_tol=1e-9)


def test_cosine_orthogonal_vectors_is_zero():
    assert math.isclose(store.cosine([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-9)


def test_cosine_handles_zero_vector_without_dividing_by_zero():
    assert store.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# Task 15 finding: a `.table("jobs").select("embedding")` pull -- exactly the
# "pulling rows is cheaper than a round trip" use case this module's own
# docstring names for cosine() -- comes back from PostgREST as a stringified
# vector ("[0.1,0.2,0.3]"), not a JSON array, because Postgres has no native
# JSON cast for the `vector` type. Confirmed live against production data by
# scripts/tune_dedup_threshold.py, which crashed with
# `TypeError: can't multiply sequence by non-int of type 'str'` before this.
# ---------------------------------------------------------------------------

def test_cosine_accepts_postgrest_stringified_vector():
    stringified = "[1.0,2.0,3.0]"
    as_list = [1.0, 2.0, 3.0]
    assert math.isclose(store.cosine(stringified, as_list), 1.0, rel_tol=1e-9)
    assert math.isclose(store.cosine(stringified, stringified), 1.0, rel_tol=1e-9)


def test_cosine_stringified_vector_matches_list_result():
    a, b = [1.0, 0.0, 2.0], [0.5, 3.0, -1.0]
    expected = store.cosine(a, b)
    assert store.cosine(json.dumps(a), json.dumps(b)) == expected


def test_similar_jobs_filters_below_threshold():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        {"job_hash": "a", "similarity": 0.97},
        {"job_hash": "b", "similarity": 0.80},
    ]
    with patch.object(store, "_db", return_value=db):
        out = store.similar_jobs_in_company("Acme", [0.1] * 768, threshold=0.93)
    assert [r["job_hash"] for r in out] == ["a"]


def test_similar_bullets_respects_k():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [{"id": str(i)} for i in range(20)]
    with patch.object(store, "_db", return_value=db):
        out = store.similar_bullets("user-1", [0.1] * 768, k=8)
    assert len(out) == 8
