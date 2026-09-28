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
from unittest.mock import MagicMock, patch

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


# --- Surgical retry: one missing column must cost exactly one column ---

@pytest.mark.parametrize("message,expected", [
    (PGRST204, "trace_id"),
    (RAW_PG, "trace_id"),
    ("Could not find the 'score_tier' column of 'jobs' in the schema cache", "score_tier"),
    ("connection reset", None),
])
def test_extracts_the_named_column(message, expected):
    assert score_batch._missing_column_name(Exception(message)) == expected


def test_retry_drops_only_the_named_column():
    """Regression: the old retry popped a hardcoded list of 13 columns whenever
    ANY one was missing. Measured 2026-09-28 -- trace_id alone being absent cost
    score_tier, key_matches, gaps, match_reasoning, archetype, seniority,
    remote, requirement_map and matched_resume on all 17 rows of a live run,
    so jobs landed with a score and no tier and the dashboard's tier filter
    had nothing to filter on."""
    good = {**SAMPLE_JOB, "description": "x" * 150}
    db = _make_supabase(jobs_raw_data=[good], resume_data=[{"tex_content": SAMPLE_RESUME_TEX}])

    sent = []

    def insert(record):
        sent.append(dict(record))
        chain = MagicMock()
        if "trace_id" in record:
            chain.execute.side_effect = Exception(PGRST204)
        else:
            chain.execute.return_value = None
        return chain

    db.table("jobs").insert.side_effect = insert

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={
             "content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}):
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"], "min_match_score": 60}, None)

    assert result["inserted"] == 1
    assert result["insert_failures"] == 0
    assert len(sent) == 2, "expected one failed attempt then one retry"
    # Only trace_id may differ between the two attempts.
    assert set(sent[0]) - set(sent[1]) == {"trace_id"}
    # The columns the old blunt retry used to discard must survive.
    for col in ("score_tier", "key_matches", "gaps", "match_reasoning",
                "archetype", "seniority", "matched_resume"):
        assert col in sent[1], f"{col} was dropped but was never the problem"


def test_unidentifiable_error_does_not_guess():
    """If the error names no column, stop -- do not start popping fields."""
    assert score_batch._missing_column_name(Exception("PGRST204 something odd")) is None


@pytest.mark.parametrize("message", [
    'column "trace_id" does not exist',
    'column "trace_id" of relation "jobs" does not exist',
])
def test_both_postgres_spellings_name_the_column(message):
    """Postgres uses both forms depending on whether the statement gave it a
    relation to name. Matching only the short one left the retry unable to
    identify the column in the common case."""
    assert score_batch._missing_column_name(Exception(message)) == "trace_id"
    assert score_batch._is_missing_column_error(Exception(message)) is True
