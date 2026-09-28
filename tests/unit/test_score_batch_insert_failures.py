"""Regression tests for the 2026-09-28 total-loss run.

That morning the pipeline scraped 1,513 jobs, scored 58, wrote zero, and
reported SUCCEEDED. Two defects combined:

  1. The retry-without-optional-columns guard tested for "does not exist",
     but Supabase returns PostgREST's wording -- "Could not find the
     'trace_id' column of 'jobs' in the schema cache" (PGRST204) -- so the
     retry never ran.
  2. The resulting per-row failure was logged at WARNING and swallowed, so
     a run where *every* insert failed was indistinguishable from a run that
     legitimately matched nothing.
"""
import json
from unittest.mock import patch

import pytest

from tests.unit.test_score_batch import (
    SAMPLE_JOB, SAMPLE_RESUME_TEX, VALID_AI_SCORE, _make_supabase,
)

import score_batch

# Verbatim from the 2026-09-28T07:37:30 CloudWatch log line.
PGRST204 = (
    "{'message': \"Could not find the 'trace_id' column of 'jobs' in the "
    "schema cache\", 'code': 'PGRST204', 'hint': None, 'details': None}"
)
RAW_PG = 'column "trace_id" does not exist'


@pytest.mark.parametrize("message", [PGRST204, RAW_PG])
def test_recognises_both_missing_column_wordings(message):
    assert score_batch._is_missing_column_error(Exception(message)) is True


@pytest.mark.parametrize("message", [
    "duplicate key value violates unique constraint",
    "new row violates row-level security policy",
    "connection timed out",
])
def test_does_not_misclassify_unrelated_errors(message):
    assert score_batch._is_missing_column_error(Exception(message)) is False


def _run_with_insert_errors(errors):
    """Drive handler() with one good job whose insert raises `errors` in turn."""
    good = {**SAMPLE_JOB, "description": "x" * 150}
    db = _make_supabase(jobs_raw_data=[good], resume_data=[{"tex_content": SAMPLE_RESUME_TEX}])
    db.table("jobs").insert.return_value.execute.side_effect = errors
    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={
             "content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}):
        return score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"], "min_match_score": 60}, None)


def test_pgrst204_triggers_the_retry_and_the_row_lands():
    """The original bug: this fell through to the swallow branch instead."""
    result = _run_with_insert_errors([Exception(PGRST204), None])  # fail, then retry succeeds
    assert result["inserted"] == 1
    assert result["insert_failures"] == 0
    assert result["matched_count"] == 1


def test_total_insert_failure_raises_instead_of_reporting_success():
    """A run that writes nothing must not return matched_count: 0 quietly."""
    with pytest.raises(RuntimeError, match="refusing to report success"):
        _run_with_insert_errors([Exception("connection reset"), Exception("connection reset")])
