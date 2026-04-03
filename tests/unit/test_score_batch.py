"""Unit tests for score_batch Lambda."""
import json
from unittest.mock import patch, MagicMock


SAMPLE_RESUME_TEX = r"""\documentclass[11pt]{article}
\begin{document}
\section*{John Doe}
Senior Software Engineer | Dublin, Ireland
\section*{Skills}
Python, AWS, FastAPI, PostgreSQL
\end{document}"""

SAMPLE_JOB = {
    "job_hash": "hash-001",
    "title": "Senior Python Engineer",
    "company": "TechCorp",
    "description": "We need a Python expert with AWS experience.",
    "location": "Dublin",
    "apply_url": "https://techcorp.com/jobs/1",
    "source": "linkedin",
}

VALID_AI_SCORE = {
    "match_score": 90,
    "ats_score": 88,
    "hiring_manager_score": 91,
    "tech_recruiter_score": 89,
    "reasoning": "Strong Python and AWS match.",
}


def _make_supabase(jobs_raw_data=None, resume_data=None, insert_ok=True):
    """Build a mock Supabase client for score_batch tests."""
    mock_client = MagicMock()

    raw_result = MagicMock()
    raw_result.data = jobs_raw_data if jobs_raw_data is not None else []

    resume_result = MagicMock()
    resume_result.data = resume_data if resume_data is not None else []

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
        insert_chain.execute.side_effect = Exception("DB error")
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
    return mock_client


def test_happy_path_returns_matched_items_with_light_touch():
    """A high-scoring AI response produces matched_items with light_touch=True."""
    db = _make_supabase(
        jobs_raw_data=[SAMPLE_JOB],
        resume_data=[{"tex_content": SAMPLE_RESUME_TEX}],
    )

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}):
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"], "min_match_score": 60},
            None,
        )

    assert result["matched_count"] == 1
    assert len(result["matched_items"]) == 1
    item = result["matched_items"][0]
    assert item["job_hash"] == "hash-001"
    assert item["user_id"] == "user-1"
    assert item["light_touch"] is True  # score=90 >= 85


def test_empty_hashes_returns_zero_count():
    """When no job hashes are provided, returns matched_count=0 immediately."""
    with patch("score_batch.get_supabase") as mock_get_db, \
         patch("score_batch.ai_complete_cached") as mock_ai:
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": []},
            None,
        )

    assert result == {"matched_items": [], "matched_count": 0}
    mock_get_db.assert_not_called()
    mock_ai.assert_not_called()


def test_malformed_ai_response_markdown_wrapped_is_handled():
    """Markdown-wrapped JSON (```json...```) is parsed correctly."""
    markdown_response = f"```json\n{json.dumps(VALID_AI_SCORE)}\n```"
    db = _make_supabase(
        jobs_raw_data=[SAMPLE_JOB],
        resume_data=[{"tex_content": SAMPLE_RESUME_TEX}],
    )

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={"content": markdown_response, "provider": "groq", "model": "llama"}):
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"], "min_match_score": 60},
            None,
        )

    assert result["matched_count"] == 1
    assert result["matched_items"][0]["job_hash"] == "hash-001"


def test_below_min_score_is_filtered_out():
    """Jobs scoring below min_match_score are not included in matched_items."""
    low_score = {**VALID_AI_SCORE, "match_score": 45}
    db = _make_supabase(
        jobs_raw_data=[SAMPLE_JOB],
        resume_data=[{"tex_content": SAMPLE_RESUME_TEX}],
    )

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(low_score), "provider": "groq", "model": "llama"}):
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"], "min_match_score": 60},
            None,
        )

    assert result["matched_count"] == 0
    assert result["matched_items"] == []


def test_no_resume_returns_error():
    """When user has no resume, returns error key and empty matched_items."""
    db = _make_supabase(
        jobs_raw_data=[SAMPLE_JOB],
        resume_data=[],  # no resume
    )

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached") as mock_ai:
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-001"]},
            None,
        )

    assert result["error"] == "no_resume"
    assert result["matched_count"] == 0
    assert result["matched_items"] == []
    mock_ai.assert_not_called()
