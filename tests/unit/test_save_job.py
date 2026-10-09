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
    jobs_chain.or_.return_value = jobs_chain
    # A real update returns the rows it changed; save_job reads that count
    # (2026-10-08) and reports saved False on zero. A bare MagicMock() here
    # has no row list and would read as "matched nothing".
    jobs_chain.execute.return_value = MagicMock(data=[{"job_id": "j-1"}])
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
    assert "application_status" not in update_payload  # user's column, not the pipeline's


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
    assert "application_status" not in update_payload  # user's column, not the pipeline's


def test_no_pdfs_writes_nothing_at_all():
    """No PDF keys means nothing to say — and nothing to write.

    This used to assert application_status='scored', a pipeline state written
    into the USER's column purely so the update dict was non-empty. It erased
    a real status (a job marked "Applied" reverted on the next run) and made
    the dashboard's Status filter unable to match ~68% of rows.
    """
    event = {**BASE_EVENT}  # no compile_result
    s3 = _make_s3_mock()
    db = _make_supabase()

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is False, "nothing was written, so saved must not claim otherwise"
    assert result["job_hash"] == "hash-abc"
    s3.generate_presigned_url.assert_not_called()
    assert db.table.return_value.update.call_args_list == [], (
        "issued a database update with nothing to update — the old code wrote "
        "application_status='scored' purely to make the dict non-empty"
    )


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
    assert "application_status" not in update_payload
    assert update_payload["failure_reason"]  # the failure is recorded here now
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
    assert "application_status" not in update_payload
    assert update_payload["failure_reason"]  # the failure is recorded here now
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
    # The local-dev tectonic stub is not a real failure, so there is nothing to
    # record and nothing to write — previously this wrote status='scored' into
    # the user's column purely to make the update dict non-empty.
    assert not db.table.return_value.update.call_args_list


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
    assert "application_status" not in update_payload  # user's column, not the pipeline's
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
    assert "application_status" not in update_payload  # user's column, not the pipeline's
    assert "cover_letter_s3_url" not in update_payload
    assert "failure_reason" not in update_payload or update_payload["failure_reason"] is None


# ---------------------------------------------------------------------------
# Cover-letter URLs could never be re-signed
# ---------------------------------------------------------------------------

def test_cover_letter_key_is_persisted_like_the_resume_key():
    """Without the key, a cover-letter link dies after 7 days and stays dead.

    save_job wrote resume_s3_url AND resume_s3_key, but for cover letters only
    the URL. _refresh_s3_urls re-signs from `cover_letter_s3_key`, which was
    therefore always None — measured 2026-09-29: 400 jobs had a
    cover_letter_s3_url and the column did not exist at all. The presigned URL
    expires and nothing can ever mint a new one, so the dashboard shows a cover
    letter button that 403s.
    """
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
        save_job.handler(event, None)

    payload = db.table.return_value.update.call_args_list[0][0][0]
    assert payload["cover_letter_s3_key"] == "covers/hash-abc.pdf", (
        "the cover-letter S3 key is still not persisted, so its URL can never "
        "be re-signed"
    )
    assert payload["resume_s3_key"] == "resumes/hash-abc.pdf"


def test_a_missing_cover_letter_key_column_does_not_lose_the_whole_row():
    """Deploy order must not matter.

    The column is added by a migration that is applied by hand — no workflow
    runs it. If code ships first, a plain UPDATE naming an absent column fails
    outright and the row keeps NOTHING, which is the PGRST204 shape that cost a
    full run on 2026-09-28. Drop the one column and write the rest.
    """
    event = {
        **BASE_EVENT,
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf"},
        "cover_compile_result": {"pdf_s3_key": "covers/hash-abc.pdf"},
    }
    s3 = _make_s3_mock()
    db = MagicMock()
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    calls = {"n": 0}

    def execute():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception(
                "{'message': \"Could not find the 'cover_letter_s3_key' column "
                "of 'jobs' in the schema cache\", 'code': 'PGRST204'}"
            )
        return MagicMock(data=[{"job_id": "j-1"}])

    chain.or_.return_value = chain
    chain.execute.side_effect = execute
    db.table.return_value = chain

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=db):
        import save_job
        result = save_job.handler(event, None)

    assert result["saved"] is True, "the whole row was lost over one missing column"
    second = chain.update.call_args_list[-1][0][0]
    assert "cover_letter_s3_key" not in second
    assert second["resume_s3_url"], "the resume URL must still be written"


# ---------------------------------------------------------------------------
# The stored resume verdict (shared/resume_verdict.py)
# ---------------------------------------------------------------------------
# Four checks were already being measured and none were aggregated, so no
# column, dashboard or alarm could answer "is this resume any good". These
# assert the aggregate lands on the row -- and, just as importantly, that it
# does NOT land on runs that measured nothing.


def _captured_update(event):
    """Run the handler and return the dict it PATCHed, or None if it did not."""
    s3 = _make_s3_mock()
    supabase = _make_supabase()
    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=supabase):
        import save_job
        save_job.handler(event, None)
    chain = supabase.table.return_value
    if not chain.update.called:
        return None
    return chain.update.call_args[0][0]


def test_a_healthy_resume_is_graded_and_stored_on_the_row():
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": []},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    assert update["resume_verdict"]["grade"] == "pass"
    assert update["resume_verdict"]["reasons"] == []


def test_a_four_page_resume_is_stored_as_a_failure_with_its_reason():
    """The verdict has to carry WHY, or the reader is sent back to the logs --
    which is the thing it replaces."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": []},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": ["4 pages, expected 2"],
                           "ats_violations": []},
    })
    verdict = update["resume_verdict"]
    assert verdict["grade"] == "fail"
    assert "pages: 4 pages, expected 2" in verdict["reasons"]


def test_a_resume_whose_writing_was_never_checked_is_not_graded_a_pass():
    """tailor_resume's fallback ships the base resume, which the writing check
    never saw. CLAUDE.md #2: a status that cannot tell "did the work" from
    "did nothing" is a lie."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": None,
                          "used_fallback": True},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    assert update["resume_verdict"]["grade"] == "unmeasured"
    assert "writing: never measured" in update["resume_verdict"]["reasons"]


def test_a_run_that_tailored_nothing_leaves_an_existing_verdict_alone():
    """The application_status lesson, applied before it can repeat.

    A cover-letter-only pass, or a SaveJobAfterError that never reached
    tailoring, measured nothing about the resume. Writing `unmeasured` there
    would erase a verdict that was true -- a pipeline state overwriting real
    data, exactly how a user's "Applied" used to revert to "ready".
    """
    update = _captured_update({
        **BASE_EVENT,
        "cover_compile_result": {"pdf_s3_key": "cover_letters/hash-abc.pdf"},
    })
    assert update is not None, "the cover letter should still have been saved"
    assert "resume_verdict" not in update


def test_a_failed_compile_is_still_graded():
    """A compile that produced no PDF measured nothing, which is a verdict in
    itself -- and the row should say so next to its failure_reason rather than
    leaving the grade from the last successful run standing."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": []},
        "compile_result": {"error": "tectonic_failed", "stderr": "! Undefined control sequence."},
    })
    assert update["resume_verdict"]["grade"] == "unmeasured"
    assert update["failure_reason"].startswith("tectonic_failed")


def test_the_verdict_column_being_absent_does_not_lose_the_resume():
    """The migration is applied BY HAND. If this code ships first, PostgREST
    fails the WHOLE update on the unknown column and the row keeps NOTHING --
    the PGRST204 shape that cost a run's writes on 2026-09-28. save_job's
    existing degrade path must cover the new column too, which is only true if
    the error names it; this asserts the resume survives either way.
    """
    s3 = _make_s3_mock()
    supabase = _make_supabase()
    chain = supabase.table.return_value
    calls = {"n": 0}

    def execute():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception(
                "{'code': 'PGRST204', 'message': \"Could not find the "
                "'resume_verdict' column of 'jobs' in the schema cache\"}")
        return MagicMock(data=[{"job_id": "j-1"}])

    chain.or_.return_value = chain
    chain.execute.side_effect = execute

    with patch("save_job.boto3", _make_boto3_mock(s3)), \
         patch("save_job.get_supabase", return_value=supabase):
        import save_job
        result = save_job.handler({
            **BASE_EVENT,
            "tailor_result": {"composition_violations": [], "quality_warnings": []},
            "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                               "page_violations": [], "ats_violations": []},
        }, None)

    assert result["has_resume"] is True
    retried = chain.update.call_args[0][0]
    assert "resume_verdict" not in retried
    assert retried["resume_s3_key"] == "resumes/hash-abc.pdf"


def test_the_local_dev_no_compile_case_is_not_graded_at_all():
    """`tectonic_not_available` means the compile was never ATTEMPTED, so there
    is no document to grade. Caught by test_tectonic_not_available_does_not_
    mark_failed when the verdict's guard was merely "did either step run" --
    recorded as its own test so the reason survives the next edit.
    """
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": []},
        "compile_result": {"error": "tectonic_not_available", "pdf_s3_key": None},
    })
    assert update is None, f"a local dry-run should write nothing, wrote {update}"


def test_surviving_guard_violations_are_stored_beside_the_grade():
    """What the council finalized best-effort WITH, recorded on the row.

    Measured 2026-10-07: 87 of 212 runs (41%) exhausted the repair budget and
    shipped with a block-severity violation present. Nothing recorded which,
    anywhere, so the figure had to be reconstructed from CloudWatch by hand for
    one batch. Storing it is what makes the false-positive measurement possible
    — which is the precondition for ever letting it block (CLAUDE.md #16).
    """
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": [],
                          "guard_violations": ["fabrication: 'Kotlin' not in base"]},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    verdict = update["resume_verdict"]
    assert verdict["guard_violations"] == ["fabrication: 'Kotlin' not in base"]


def test_surviving_guard_violations_do_not_change_the_grade():
    """Recorded, not graded. Their false-positive rate at this position has
    never been measured, and #16 is explicit that measurement comes first.

    This is the assertion that would fail if someone wired them into the grade
    without taking that measurement — which is the whole point of writing it
    down rather than relying on the comment."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": [],
                          "guard_violations": ["fabrication: 'Kotlin' not in base"]},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    verdict = update["resume_verdict"]
    assert verdict["grade"] == "pass"
    assert "guard" not in verdict["checks"], (
        "a 5th REQUIRED check would grade every legacy-engine and backfilled "
        "row `unmeasured`, destroying the gate's signal corpus-wide")


def test_an_engine_with_no_guard_nodes_records_nothing_rather_than_nothing_wrong():
    """`None` from the legacy engine means "never measured", and must not be
    stored as an empty list — that would claim a clean guard verdict from an
    engine that has no guard nodes at all."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": [],
                          "guard_violations": None},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    assert "guard_violations" not in update["resume_verdict"]


def test_stripped_fabrications_are_stored_beside_the_grade():
    """tailor_resume now cuts a flagged list entry out of the tailored body
    rather than discarding the tailoring. What was cut is recorded inside the
    resume_verdict JSON (no new column), and the grade is `warn` -- the strip
    note is a measured, advisory writing finding -- never a clean `pass`."""
    note = "fabrication stripped: 'Rust' removed from the header/Skills list"
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": [note],
                          "fabrications_stripped": ["Rust"]},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    verdict = update["resume_verdict"]
    assert verdict["fabrications_stripped"] == ["Rust"]
    assert verdict["grade"] == "warn"


def test_a_legacy_tailor_result_records_no_strip_field():
    """Absent is not `[]`: a tailor result from before stripping existed did
    not claim that nothing was stripped."""
    update = _captured_update({
        **BASE_EVENT,
        "tailor_result": {"composition_violations": [], "quality_warnings": []},
        "compile_result": {"pdf_s3_key": "resumes/hash-abc.pdf",
                           "page_violations": [], "ats_violations": []},
    })
    assert "fabrications_stripped" not in update["resume_verdict"]
