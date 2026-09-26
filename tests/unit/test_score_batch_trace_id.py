"""Task 10: trace_id persisted onto the `jobs` row from score_batch.py.

Kept as its own file (rather than added to tests/unit/test_score_batch.py)
to avoid touching a shared test file other concurrent work may also be
editing — score_batch.py itself is owned here only "for persisting a trace
id" (see the Task 9/10 report), and this is that narrow slice's test.

Today score_single_job's only AI call (ai_complete_cached -> ai_complete ->
the single-model failover chain) never produces a trace_id -- only
council_complete_langgraph does, and scoring does not go through the council
(see task-9-10-report.md). So `trace_id` is always None in current
production behaviour; these tests prove the plumbing carries a trace_id
through WHEN one is present (forward-compatible) and degrades safely when
the `jobs.trace_id` column does not exist yet (defensive, matching the
existing retry-without-optional-columns pattern already in this file).
"""
import json
from unittest.mock import MagicMock, patch

SAMPLE_RESUME_TEX = r"""\documentclass[11pt]{article}
\begin{document}
\section*{Jane Doe}
Senior Software Engineer
\end{document}"""

SAMPLE_JOB = {
    "job_hash": "hash-trace-1",
    "title": "Senior Python Engineer",
    "company": "TraceCorp",
    "description": "We need a Python expert with AWS experience. You will build scalable microservices, design APIs, and work with Docker and Kubernetes in a cloud-native environment.",
    "location": "Dublin",
    "apply_url": "https://tracecorp.com/jobs/1",
    "source": "linkedin",
}

VALID_AI_SCORE = {
    "match_score": 90,
    "ats_score": 88,
    "hiring_manager_score": 91,
    "tech_recruiter_score": 89,
    "reasoning": "Strong match.",
}


def _make_supabase(insert_ok=True):
    mock_client = MagicMock()

    raw_result = MagicMock()
    raw_result.data = [SAMPLE_JOB]
    resume_result = MagicMock()
    resume_result.data = [{"tex_content": SAMPLE_RESUME_TEX}]
    insert_result = MagicMock()

    raw_chain = MagicMock()
    raw_chain.select.return_value = raw_chain
    raw_chain.in_.return_value = raw_chain
    raw_chain.execute.return_value = raw_result

    resume_chain = MagicMock()
    resume_chain.select.return_value = resume_chain
    resume_chain.eq.return_value = resume_chain
    resume_chain.order.return_value = resume_chain
    resume_chain.limit.return_value = resume_chain
    resume_chain.execute.return_value = resume_result

    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    if not insert_ok:
        # First insert (with trace_id) fails as it would against a database
        # that hasn't run the 20260926140000 migration yet; the RETRY
        # (score_batch.py's existing drop-optional-columns-and-retry path)
        # must succeed, or this mock would wrongly fail every attempt.
        insert_chain.execute.side_effect = [
            Exception('column "trace_id" of relation "jobs" does not exist'),
            insert_result,
        ]
    else:
        insert_chain.execute.return_value = insert_result

    def table_side_effect(name):
        if name == "jobs_raw":
            return raw_chain
        elif name == "user_resumes":
            return resume_chain
        elif name == "jobs":
            return insert_chain
        return MagicMock()

    mock_client.table.side_effect = table_side_effect
    return mock_client, insert_chain


def test_score_single_job_carries_trace_id_from_response_dict():
    """The forward-compat seam: if the underlying AI call ever returns a
    trace_id (only council_complete_langgraph does today, and scoring
    doesn't call it), score_single_job's result must not drop it on the
    floor -- it explicitly copies `provider`/`model` off response_dict
    already; trace_id must be copied the same way.
    """
    with patch("score_batch.ai_complete_cached", return_value={
        "content": json.dumps(VALID_AI_SCORE),
        "provider": "groq", "model": "llama",
        "trace_id": "11111111-1111-1111-1111-111111111111",
    }):
        import score_batch
        result = score_batch.score_single_job(SAMPLE_JOB, SAMPLE_RESUME_TEX)

    assert result["trace_id"] == "11111111-1111-1111-1111-111111111111"


def test_score_single_job_trace_id_is_none_when_absent():
    """Today's actual behaviour: ai_complete_cached never returns a
    trace_id, so score_single_job's result must have a clean None, not a
    KeyError or a fabricated value.
    """
    with patch("score_batch.ai_complete_cached", return_value={
        "content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama",
    }):
        import score_batch
        result = score_batch.score_single_job(SAMPLE_JOB, SAMPLE_RESUME_TEX)

    assert result["trace_id"] is None


def test_handler_persists_trace_id_onto_job_record():
    db, insert_chain = _make_supabase()
    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={
             "content": json.dumps(VALID_AI_SCORE),
             "provider": "groq", "model": "llama",
             "trace_id": "22222222-2222-2222-2222-222222222222",
         }):
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-trace-1"], "min_match_score": 60},
            None,
        )

    assert result["matched_count"] == 1
    inserted = insert_chain.insert.call_args[0][0]
    assert inserted["trace_id"] == "22222222-2222-2222-2222-222222222222"


def test_handler_survives_missing_trace_id_column():
    """jobs.trace_id does not exist until the 20260926140000 migration is
    applied. Until then, insert must retry without it (the same defensive
    pattern already used for key_matches/gaps/etc.) rather than dropping the
    whole job match.
    """
    db, insert_chain = _make_supabase(insert_ok=False)
    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={
             "content": json.dumps(VALID_AI_SCORE),
             "provider": "groq", "model": "llama",
             "trace_id": "33333333-3333-3333-3333-333333333333",
         }):
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-trace-1"], "min_match_score": 60},
            None,
        )

    # First insert attempt (with trace_id) raised; handler must have retried
    # with the optional columns -- including trace_id -- stripped out, and
    # still recorded the match rather than silently dropping the job.
    assert insert_chain.insert.call_count == 2
    retried_record = insert_chain.insert.call_args_list[1][0][0]
    assert "trace_id" not in retried_record
    assert result["matched_count"] == 1
