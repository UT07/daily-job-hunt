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
    "description": "We need a Python expert with AWS experience. You will build scalable microservices, design APIs, and work with Docker and Kubernetes in a cloud-native environment.",
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


# ── score_single_job temperature tests ──


def test_score_single_job_passes_temperature_to_ai():
    """score_single_job forwards the temperature kwarg to ai_complete_cached."""
    with patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}) as mock_ai:
        import score_batch
        score_batch.score_single_job(SAMPLE_JOB, SAMPLE_RESUME_TEX, temperature=0)

    mock_ai.assert_called_once()
    _, kwargs = mock_ai.call_args
    assert kwargs["temperature"] == 0


def test_score_single_job_default_temperature_is_zero():
    """score_single_job defaults to temperature=0 for deterministic scoring."""
    with patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}) as mock_ai:
        import score_batch
        score_batch.score_single_job(SAMPLE_JOB, SAMPLE_RESUME_TEX)

    mock_ai.assert_called_once()
    _, kwargs = mock_ai.call_args
    assert kwargs["temperature"] == 0


def test_score_single_job_computes_match_score_when_missing():
    """When AI omits match_score, score_single_job computes the average."""
    score_no_match = {
        "ats_score": 80,
        "hiring_manager_score": 90,
        "tech_recruiter_score": 70,
        "reasoning": "Good fit.",
    }
    with patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(score_no_match), "provider": "groq", "model": "llama"}):
        import score_batch
        result = score_batch.score_single_job(SAMPLE_JOB, SAMPLE_RESUME_TEX)

    assert result is not None
    assert result["match_score"] == round((80 + 90 + 70) / 3)


# ── score_single_job_deterministic tests ──


def test_deterministic_returns_median_of_three_calls():
    """Median of 3 varied scores should be the middle value for each dimension."""
    scores = [
        {"content": json.dumps({"ats_score": 80, "hiring_manager_score": 75, "tech_recruiter_score": 85, "match_score": 80.0}), "provider": "groq", "model": "llama"},
        {"content": json.dumps({"ats_score": 90, "hiring_manager_score": 70, "tech_recruiter_score": 88, "match_score": 82.7}), "provider": "groq", "model": "llama"},
        {"content": json.dumps({"ats_score": 85, "hiring_manager_score": 80, "tech_recruiter_score": 82, "match_score": 82.3}), "provider": "groq", "model": "llama"},
    ]
    call_count = {"n": 0}

    def mock_ai(*args, **kwargs):
        result = scores[call_count["n"]]
        call_count["n"] += 1
        return result

    with patch("score_batch.ai_complete_cached", side_effect=mock_ai):
        import score_batch
        result = score_batch.score_single_job_deterministic(SAMPLE_JOB, SAMPLE_RESUME_TEX, num_calls=3)

    assert result is not None
    assert result["ats_score"] == 85        # median of 80, 90, 85
    assert result["hiring_manager_score"] == 75  # median of 75, 70, 80
    assert result["tech_recruiter_score"] == 85  # median of 85, 88, 82
    assert result["match_score"] == 82.3    # median of 80.0, 82.7, 82.3


def test_deterministic_returns_none_when_all_calls_fail():
    """When all AI calls fail, deterministic returns None."""
    with patch("score_batch.ai_complete_cached", side_effect=RuntimeError("provider down")):
        import score_batch
        result = score_batch.score_single_job_deterministic(SAMPLE_JOB, SAMPLE_RESUME_TEX, num_calls=3)

    assert result is None


def test_deterministic_returns_single_result_when_only_one_succeeds():
    """When only 1 of 3 calls succeeds, return that single result."""
    call_count = {"n": 0}

    def mock_ai(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return {"content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}
        raise RuntimeError("provider down")

    with patch("score_batch.ai_complete_cached", side_effect=mock_ai):
        import score_batch
        result = score_batch.score_single_job_deterministic(SAMPLE_JOB, SAMPLE_RESUME_TEX, num_calls=3)

    assert result is not None
    assert result["ats_score"] == VALID_AI_SCORE["ats_score"]
    assert result["match_score"] == VALID_AI_SCORE["match_score"]


def test_deterministic_handles_two_successful_calls():
    """When 2 of 3 calls succeed, median of 2 values equals their average."""
    scores = [
        {"content": json.dumps({"ats_score": 80, "hiring_manager_score": 70, "tech_recruiter_score": 85, "match_score": 78.3}), "provider": "groq", "model": "llama"},
        {"content": json.dumps({"ats_score": 90, "hiring_manager_score": 76, "tech_recruiter_score": 88, "match_score": 84.7}), "provider": "groq", "model": "llama"},
    ]
    call_count = {"n": 0}

    def mock_ai(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return scores[call_count["n"] - 1]
        raise RuntimeError("provider down")

    with patch("score_batch.ai_complete_cached", side_effect=mock_ai):
        import score_batch
        result = score_batch.score_single_job_deterministic(SAMPLE_JOB, SAMPLE_RESUME_TEX, num_calls=3)

    assert result is not None
    # median of 2 values = average
    assert result["ats_score"] == 85       # median of 80, 90
    assert result["hiring_manager_score"] == 73  # median of 70, 76 = 73
    assert result["tech_recruiter_score"] == 86  # median of 85, 88 = 86 (int of 86.5)


# ── should_skip_scoring tests ──


def test_should_skip_scoring_empty_description():
    """Job with empty description returns 'insufficient_data'."""
    import score_batch
    job = {"description": "", "company": "Acme"}
    assert score_batch.should_skip_scoring(job) == "insufficient_data"


def test_should_skip_scoring_none_description():
    """Job with None description returns 'insufficient_data'."""
    import score_batch
    job = {"description": None, "company": "Acme"}
    assert score_batch.should_skip_scoring(job) == "insufficient_data"


def test_should_skip_scoring_short_description():
    """Job with description < 100 chars returns 'insufficient_data'."""
    import score_batch
    job = {"description": "A short job posting.", "company": "Acme"}
    assert len(job["description"]) < 100
    assert score_batch.should_skip_scoring(job) == "insufficient_data"


def test_should_skip_scoring_missing_company():
    """Job with empty company returns 'incomplete'."""
    import score_batch
    job = {"description": "x" * 150, "company": ""}
    assert score_batch.should_skip_scoring(job) == "incomplete"


def test_should_skip_scoring_whitespace_only_company():
    """Job with whitespace-only company returns 'incomplete'."""
    import score_batch
    job = {"description": "x" * 150, "company": "   "}
    assert score_batch.should_skip_scoring(job) == "incomplete"


def test_should_skip_scoring_none_company():
    """Job with None company returns 'incomplete'."""
    import score_batch
    job = {"description": "x" * 150, "company": None}
    assert score_batch.should_skip_scoring(job) == "incomplete"


def test_should_skip_scoring_valid_job():
    """Valid tech job with sufficient description, company, and title returns None."""
    import score_batch
    job = {"description": "x" * 150, "company": "TechCorp", "title": "Software Engineer"}
    assert score_batch.should_skip_scoring(job) is None


def test_should_skip_scoring_exactly_100_chars():
    """Job with exactly 100-char description passes (not < 100)."""
    import score_batch
    job = {"description": "x" * 100, "company": "TechCorp", "title": "Backend Developer"}
    assert score_batch.should_skip_scoring(job) is None


def test_should_skip_scoring_rejects_non_tech_title():
    """Obvious non-tech titles are rejected before AI scoring."""
    import score_batch
    job = {"description": "x" * 200, "company": "HealthCorp", "title": "Registered Nurse"}
    assert score_batch.should_skip_scoring(job) == "non_tech_role"


def test_should_skip_scoring_rejects_no_tech_keywords():
    """Titles without tech keywords are rejected."""
    import score_batch
    job = {"description": "x" * 200, "company": "Foo", "title": "Chief Happiness Officer"}
    assert score_batch.should_skip_scoring(job) == "no_tech_keywords"


def test_handler_skips_bad_data_jobs():
    """Handler skips jobs with insufficient data and does not call AI for them."""
    short_job = {
        "job_hash": "hash-short",
        "title": "Engineer",
        "company": "Acme",
        "description": "Too short.",
        "source": "linkedin",
    }
    good_job = {**SAMPLE_JOB, "description": "x" * 150}

    db = _make_supabase(
        jobs_raw_data=[short_job, good_job],
        resume_data=[{"tex_content": SAMPLE_RESUME_TEX}],
    )

    with patch("score_batch.get_supabase", return_value=db), \
         patch("score_batch.ai_complete_cached", return_value={"content": json.dumps(VALID_AI_SCORE), "provider": "groq", "model": "llama"}) as mock_ai:
        import score_batch
        result = score_batch.handler(
            {"user_id": "user-1", "new_job_hashes": ["hash-short", "hash-001"], "min_match_score": 60},
            None,
        )

    # AI is only called for the good job (short job is skipped before scoring).
    # num_calls was reduced from 3 → 1 to cut scoring cost (temp=0 is deterministic).
    assert mock_ai.call_count == 1
    assert result["skipped_count"] == 1
    assert result["matched_count"] == 1


# ── compute_base_scores / compute_tailored_scores tests ──


def test_compute_base_scores():
    """compute_base_scores returns base_* keys from deterministic scoring."""
    det_result = {
        "ats_score": 70,
        "hiring_manager_score": 65,
        "tech_recruiter_score": 72,
        "match_score": 69.0,
    }
    with patch("score_batch.score_single_job_deterministic", return_value=det_result):
        import score_batch
        result = score_batch.compute_base_scores({"title": "Test"}, "resume text")

    assert result["base_ats_score"] == 70
    assert result["base_hm_score"] == 65
    assert result["base_tr_score"] == 72
    assert result["match_score"] == 69.0


def test_compute_tailored_scores():
    """compute_tailored_scores returns tailored_* keys from deterministic scoring."""
    det_result = {
        "ats_score": 85,
        "hiring_manager_score": 80,
        "tech_recruiter_score": 82,
        "match_score": 82.3,
    }
    with patch("score_batch.score_single_job_deterministic", return_value=det_result):
        import score_batch
        result = score_batch.compute_tailored_scores({"title": "Test"}, "tailored resume")

    assert result["tailored_ats_score"] == 85
    assert result["tailored_hm_score"] == 80
    assert result["tailored_tr_score"] == 82
    assert result["final_score"] == 82.3


def test_compute_base_scores_handles_failure():
    """compute_base_scores returns empty dict when deterministic scoring fails."""
    with patch("score_batch.score_single_job_deterministic", return_value=None):
        import score_batch
        result = score_batch.compute_base_scores({"title": "Test"}, "resume")

    assert result == {}


def test_compute_tailored_scores_handles_failure():
    """compute_tailored_scores returns empty dict when deterministic scoring fails."""
    with patch("score_batch.score_single_job_deterministic", return_value=None):
        import score_batch
        result = score_batch.compute_tailored_scores({"title": "Test"}, "tailored resume")

    assert result == {}


# ── score_writing_quality tests ──


def test_writing_quality_scoring():
    """score_writing_quality returns dimension scores and computed average."""
    ai_response = {
        "content": '{"specificity": 8, "impact_language": 7, "authenticity": 9, "readability": 8}'
    }
    with patch("score_batch.ai_complete_cached", return_value=ai_response):
        import score_batch
        result = score_batch.score_writing_quality("tailored resume text")

    assert result["writing_quality_score"] == 8.0
    assert result["specificity"] == 8
    assert result["impact_language"] == 7
    assert result["authenticity"] == 9
    assert result["readability"] == 8


def test_writing_quality_handles_bad_response():
    """score_writing_quality returns None score when AI returns non-JSON."""
    with patch("score_batch.ai_complete_cached", return_value={"content": "I cannot rate this resume"}):
        import score_batch
        result = score_batch.score_writing_quality("resume")

    assert result["writing_quality_score"] is None


def test_writing_quality_handles_markdown_fences():
    """score_writing_quality strips markdown code fences before parsing."""
    ai_response = {
        "content": '```json\n{"specificity": 7, "impact_language": 6, "authenticity": 8, "readability": 7}\n```'
    }
    with patch("score_batch.ai_complete_cached", return_value=ai_response):
        import score_batch
        result = score_batch.score_writing_quality("resume")

    assert result["writing_quality_score"] == 7.0
    assert result["specificity"] == 7
    assert result["impact_language"] == 6


# ── assign_model_for_ab_test tests ──


def test_model_ab_splits_jobs():
    """A/B test assigns ~80% primary and ~20% alternate across 100 jobs."""
    import random
    random.seed(42)
    from lambdas.pipeline.score_batch import assign_model_for_ab_test
    assignments = [assign_model_for_ab_test(["groq", "qwen"]) for _ in range(100)]
    primary = assignments.count("groq")
    alternate = assignments.count("qwen")
    assert 60 < primary < 95
    assert 5 < alternate < 40


def test_model_ab_single_provider():
    """With a single provider, always returns that provider."""
    from lambdas.pipeline.score_batch import assign_model_for_ab_test
    assert assign_model_for_ab_test(["groq"]) == "groq"


def test_model_ab_empty_providers():
    """With no providers, returns None."""
    from lambdas.pipeline.score_batch import assign_model_for_ab_test
    assert assign_model_for_ab_test([]) is None
