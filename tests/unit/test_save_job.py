"""Unit tests for save_job Lambda."""
from unittest.mock import patch, MagicMock


def _make_s3_mock(presigned_url="https://s3.example.com/presigned"):
    """Return a mock boto3 S3 client."""
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = presigned_url
    return mock_s3


def _make_boto3_mock(s3_mock):
    """Return a mock boto3 module whose client() yields s3_mock."""
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = s3_mock
    return mock_boto3


def _make_supabase():
    """Return a mock Supabase client for save_job tests."""
    mock_client = MagicMock()
    jobs_chain = MagicMock()
    jobs_chain.update.return_value = jobs_chain
    jobs_chain.eq.return_value = jobs_chain
    jobs_chain.execute.return_value = MagicMock()
    mock_client.table.return_value = jobs_chain
    return mock_client


BASE_EVENT = {
    "job_hash": "hash-abc",
    "user_id": "user-1",
}


def test_both_pdfs_present_generates_presigned_urls_and_saves():
    """When both resume and cover letter PDF keys are present, presigned URLs are generated and saved."""
    event = {
        **BASE_EVENT,
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf"},
        "cover_compile_result": {"pdf_s3_key": "covers/hash-abc.pdf"},
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is True
    assert result["job_hash"] == "hash-abc"
    # presigned URL called twice (resume + cover letter)
    assert s3.generate_presigned_url.call_count == 2

    # update() should have been called with both URL fields + status
    update_calls = db.table.return_value.update.call_args_list
    assert len(update_calls) == 1
    update_payload = update_calls[0][0][0]
    assert "resume_s3_url" in update_payload
    assert "cover_letter_s3_url" in update_payload
    assert update_payload["application_status"] == "ready"


def test_missing_cover_letter_still_saves_resume():
    """When only resume PDF key is present, save proceeds without cover_letter_s3_url."""
    event = {
        **BASE_EVENT,
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf"},
        # no cover_compile_result
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is True
    assert s3.generate_presigned_url.call_count == 1

    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert "resume_s3_url" in update_payload
    assert "cover_letter_s3_url" not in update_payload
    assert update_payload["application_status"] == "ready"


def test_no_pdfs_still_saves_status_only():
    """When no PDF keys are present, save_job still sets application_status='scored'."""
    event = {**BASE_EVENT}  # no compile_result
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is True
    assert result["job_hash"] == "hash-abc"
    s3.generate_presigned_url.assert_not_called()
    # Should still update status to 'scored' even without PDFs (SaveJobAfterError path)
    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert update_payload["application_status"] == "scored"


def test_compile_error_marks_job_failed():
    """Bug X1 fix: compile_latex error dict (no pdf_s3_key) sets status='failed' + failure_reason."""
    event = {
        **BASE_EVENT,
        "compile_result": {
            "error": "compilation_failed",
            "stderr": "! LaTeX Error: Undefined control sequence \\foo",
            "tex_s3_key": "tex/hash-abc.tex",
        },
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is True
    assert result["failed"] is True
    assert result["has_resume"] is False
    s3.generate_presigned_url.assert_not_called()

    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert update_payload["application_status"] == "failed"
    assert update_payload["failure_reason"].startswith("compilation_failed: ")
    assert "Undefined control sequence" in update_payload["failure_reason"]
    assert "resume_s3_url" not in update_payload


def test_compile_no_pdf_output_marks_job_failed():
    """no_pdf_output (tectonic exit 0 but PDF missing) also marks the job failed."""
    event = {
        **BASE_EVENT,
        "compile_result": {"error": "no_pdf_output", "tex_s3_key": "tex/hash-abc.tex"},
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        save_job.handler(event, None)

    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert update_payload["application_status"] == "failed"
    assert update_payload["failure_reason"] == "no_pdf_output"


def test_tectonic_not_available_does_not_mark_failed():
    """Local-dev tectonic-missing error is NOT a failure — pipeline can still save partial state."""
    event = {
        **BASE_EVENT,
        "compile_result": {"error": "tectonic_not_available", "pdf_s3_key": None},
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["failed"] is False
    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    # Falls through to "no PDFs" path → 'scored', not 'failed'.
    assert update_payload["application_status"] == "scored"
    assert "failure_reason" not in update_payload


def test_successful_compile_clears_prior_failure_reason():
    """Re-running compile after a fix clears the stale failure_reason."""
    event = {
        **BASE_EVENT,
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf"},
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        save_job.handler(event, None)

    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert update_payload["application_status"] == "ready"
    assert update_payload["failure_reason"] is None


def test_cover_letter_compile_failure_does_not_fail_job():
    """Cover letter compile failure is non-fatal — resume PDF still saved, status='ready'."""
    event = {
        **BASE_EVENT,
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf"},
        "cover_compile_result": {
            "error": "compilation_failed",
            "stderr": "! Missing $",
            "tex_s3_key": "tex/cl-hash-abc.tex",
        },
    }
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["failed"] is False
    assert result["has_resume"] is True

    update_payload = db.table.return_value.update.call_args_list[0][0][0]
    assert update_payload["application_status"] == "ready"
    assert "cover_letter_s3_url" not in update_payload
    assert "failure_reason" not in update_payload or update_payload["failure_reason"] is None
