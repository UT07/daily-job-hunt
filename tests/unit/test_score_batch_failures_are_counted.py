"""A scoring run that scored nothing must not report success.

THE DEFECTS (audit, 2026-10-08), CLAUDE.md #2 — what would this report on a
no-op run?

1. `if score_result is None: continue` was silent and uncounted. With every AI
   call failing, the handler returned matched_count 0, inserted 0,
   insert_failures 0 — exactly what a run over uninteresting jobs returns —
   and the "all inserts failed" guard could not fire because nothing was
   inserted.
2. A missing resume RETURNED {"error": "no_resume"}. Step Functions treats a
   returned dict as success, so the Catch never saw it.
3. In the daily state machine a raising ScoreChunk is caught into
   ScoreChunkFailed (a Pass) and AggregateScores ignored the error key, so even
   a raise ended in PipelineComplete (Succeed). AggregateScores now raises when
   EVERY chunk failed, which its own Catch routes to NotifyError ->
   PipelineFailedAfterError (Fail).
"""
import json
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, "lambdas/pipeline")

from tests.unit.test_score_batch import (  # noqa: E402
    SAMPLE_JOB,
    SAMPLE_RESUME_TEX,
    VALID_AI_SCORE,
    _make_supabase,
)

import aggregate_scores  # noqa: E402
import score_batch  # noqa: E402

JOB2 = {**SAMPLE_JOB, "job_hash": "hash-002"}


def _run(ai, jobs=(SAMPLE_JOB,), resume=True):
    db = _make_supabase(jobs_raw_data=list(jobs),
                        resume_data=[{"tex_content": SAMPLE_RESUME_TEX}] if resume else [])
    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", **ai):
        return score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": [j["job_hash"] for j in jobs],
             "min_match_score": 60}, None)


def test_the_double_produces_a_none_score():
    """Prove the failure mode reaches `score_result is None`, not an exception
    that would raise for an unrelated reason."""
    with patch("score_batch.ai_complete_cached", side_effect=RuntimeError("all providers down")):
        assert score_batch.score_single_job_deterministic(SAMPLE_JOB, SAMPLE_RESUME_TEX) is None


def test_every_score_failing_raises():
    with pytest.raises(score_batch.ScoreBatchError, match="scored none"):
        _run({"side_effect": RuntimeError("all providers down")}, jobs=(SAMPLE_JOB, JOB2))


def test_a_partial_failure_is_counted_not_raised():
    calls = iter([RuntimeError("one down"),
                  {"content": json.dumps(VALID_AI_SCORE), "provider": "g", "model": "m"}])

    def ai(*a, **k):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r
    out = _run({"side_effect": ai}, jobs=(SAMPLE_JOB, JOB2))
    assert out["score_failures"] == 1
    assert out["scored"] == 1


def test_a_clean_run_reports_zero_failures():
    out = _run({"return_value": {"content": json.dumps(VALID_AI_SCORE),
                                 "provider": "g", "model": "m"}})
    assert out["score_failures"] == 0 and out["scored"] == 1


def test_no_resume_raises_instead_of_returning():
    with pytest.raises(score_batch.ScoreBatchError, match="no_resume"):
        _run({"return_value": None}, resume=False)


def test_an_empty_resume_row_raises_too():
    """The second no_resume branch. The shared accessor normally filters such a
    row out, so it is reached here by stubbing the accessor's result."""
    from types import SimpleNamespace
    fake = SimpleNamespace(skipped=0, row={"tex_content": ""}, tex="")
    db = _make_supabase(jobs_raw_data=[SAMPLE_JOB])
    with patch("score_batch.get_supabase", return_value=db), \
         patch("shared.resume_format.fetch_tailorable_resume", return_value=fake), \
         pytest.raises(score_batch.ScoreBatchError, match="no_resume"):
        score_batch.handler({"user_id": "u", "new_job_hashes": ["hash-001"]}, None)


class TestAggregate:
    def test_every_chunk_failed_raises(self):
        failed = {"matched_items": [], "matched_count": 0, "skipped_count": 0,
                  "error": "chunk_scoring_failed"}
        with pytest.raises(RuntimeError, match="every"):
            aggregate_scores.handler({"score_chunks": [failed, dict(failed)]}, None)

    def test_some_chunks_failed_is_counted(self):
        ok = {"matched_items": [{"job_hash": "h"}], "matched_count": 1, "skipped_count": 0}
        failed = {"matched_items": [], "matched_count": 0, "error": "chunk_scoring_failed"}
        out = aggregate_scores.handler({"score_chunks": [ok, failed]}, None)
        assert out["failed_chunks"] == 1 and out["matched_count"] == 1

    def test_no_chunks_is_not_a_failure(self):
        assert aggregate_scores.handler({"score_chunks": []}, None)["matched_count"] == 0

    def test_the_pass_state_carries_the_error_key_this_reads(self):
        """The aggregator keys on `error`; prove the real ScoreChunkFailed Result
        sets it, rather than trusting a fixture typed into this file (#6)."""
        import pathlib
        tpl = pathlib.Path("template.yaml").read_text()
        assert '"error": "chunk_scoring_failed"' in tpl
