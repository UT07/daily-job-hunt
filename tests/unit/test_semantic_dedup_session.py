"""Regression tests for the SemanticDedupSession guard.

The bug this guards: merge_dedup called find_semantic_duplicate() bare inside
the loop that persists every scraped job. One Gemini embed plus one Supabase
RPC per job, ~1,500 network round trips on a full run -- and a single raised
exception aborted the step, losing the entire day's scrape.

These tests stub should_disable_after_failure() so they assert the guard's
behaviour rather than any particular circuit-breaker threshold.
"""
from unittest.mock import patch

import pytest

from retrieval import dedup

JOB = {"job_hash": "new", "company": "TREQS", "title": "Backend Engineer"}


def test_passes_through_a_real_match():
    with patch.object(dedup, "find_semantic_duplicate", return_value={"job_hash": "old"}):
        s = dedup.SemanticDedupSession()
        assert s.find_duplicate(JOB) == {"job_hash": "old"}
        assert s.stats() == {"attempted": 1, "failed": 0, "disabled_early": False}


def test_a_failure_keeps_the_job_instead_of_raising():
    """The core regression: a retrieval failure must not propagate."""
    with patch.object(dedup, "find_semantic_duplicate", side_effect=RuntimeError("gemini 503")), \
         patch.object(dedup, "should_disable_after_failure", return_value=False):
        s = dedup.SemanticDedupSession()
        assert s.find_duplicate(JOB) is None  # not a duplicate -> job is kept
        assert s.stats()["failed"] == 1


def test_consecutive_resets_after_a_success():
    calls = [RuntimeError("blip"), {"job_hash": "old"}]
    with patch.object(dedup, "find_semantic_duplicate",
                      side_effect=lambda j: (_ for _ in ()).throw(calls[0])
                      if isinstance(calls[0], Exception) else calls[0]), \
         patch.object(dedup, "should_disable_after_failure", return_value=False):
        s = dedup.SemanticDedupSession()
        s.find_duplicate(JOB)
        assert s.consecutive == 1
        calls[0] = {"job_hash": "old"}
        s.find_duplicate(JOB)
        assert s.consecutive == 0


def test_breaker_short_circuits_later_jobs():
    """Once tripped, no further network calls are attempted."""
    with patch.object(dedup, "find_semantic_duplicate",
                      side_effect=RuntimeError("outage")) as lookup, \
         patch.object(dedup, "should_disable_after_failure", return_value=True):
        s = dedup.SemanticDedupSession()
        s.find_duplicate(JOB)
        assert s.disabled is True
        for _ in range(5):
            assert s.find_duplicate(JOB) is None
        assert lookup.call_count == 1  # not 6
        assert s.stats()["disabled_early"] is True


def test_policy_is_implemented():
    """should_disable_after_failure must be a real policy, not a stub."""
    try:
        result = dedup.should_disable_after_failure(attempted=10, failed=10, consecutive=10)
    except NotImplementedError:
        pytest.fail("should_disable_after_failure is still unimplemented")
    assert isinstance(result, bool)
    assert dedup.should_disable_after_failure(attempted=1, failed=0, consecutive=0) is False, \
        "must not disable when nothing has failed"


# --- Integration: the actual regression, through merge_dedup.handler() ---

_GOOD_DESC = (
    "We are looking for a Python developer with experience in AWS, Kubernetes, "
    "and Docker. The role involves building microservices and CI/CD pipelines "
    "for our cloud-native platform. You will work with React frontends and "
    "FastAPI backends."
)


# Deliberately distinct titles AND companies: near-identical ones get collapsed
# by the Tier 0-2 hash/fuzzy passes before Tier 4 semantic ever runs, which
# would make these tests assert against 1 job instead of 3.
_DISTINCT = [
    ("Backend Engineer", "Stripe"),
    ("Platform Engineer", "Intercom"),
    ("Site Reliability Engineer", "Workday"),
]


def _jobs_raw():
    return [
        {"job_hash": f"hash-{i}", "title": title, "company": company,
         "source": "linkedin", "description": _GOOD_DESC, "location": "Dublin"}
        for i, (title, company) in enumerate(_DISTINCT)
    ]


def test_merge_dedup_survives_a_retrieval_outage(monkeypatch):
    """With SEMANTIC_DEDUP=on and retrieval hard-down, the step must still
    return every job. Before the guard this raised out of handler() and the
    whole day's scrape was lost."""
    from tests.unit.test_merge_dedup import _make_supabase

    monkeypatch.setenv("SEMANTIC_DEDUP", "on")
    db = _make_supabase(jobs_raw_data=_jobs_raw(), existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db), \
         patch.object(dedup, "find_semantic_duplicate", side_effect=RuntimeError("gemini 503")), \
         patch.object(dedup, "should_disable_after_failure", return_value=False):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert result["total_new"] == 3, "an outage must not drop jobs"
    assert result["semantic_dedup"]["failed"] == 3
    assert result["semantic_dedup"]["attempted"] == 3


def test_step_output_distinguishes_degraded_from_clean(monkeypatch):
    """'no duplicates found' and 'dedup was broken' must not look identical."""
    from tests.unit.test_merge_dedup import _make_supabase

    monkeypatch.setenv("SEMANTIC_DEDUP", "on")
    db = _make_supabase(jobs_raw_data=_jobs_raw(), existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db), \
         patch.object(dedup, "find_semantic_duplicate", return_value=None):
        import merge_dedup
        clean = merge_dedup.handler({"user_id": "user-1"}, None)

    assert clean["semantic_dedup"] == {"attempted": 3, "failed": 0, "disabled_early": False}


def test_flag_off_adds_no_semantic_key(monkeypatch):
    from tests.unit.test_merge_dedup import _make_supabase

    monkeypatch.setenv("SEMANTIC_DEDUP", "off")
    db = _make_supabase(jobs_raw_data=_jobs_raw(), existing_jobs_data=[])

    with patch("merge_dedup.get_supabase", return_value=db):
        import merge_dedup
        result = merge_dedup.handler({"user_id": "user-1"}, None)

    assert "semantic_dedup" not in result
    assert result["total_new"] == 3
