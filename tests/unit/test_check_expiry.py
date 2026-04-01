"""Unit tests for check_expiry Lambda."""
import pytest
import respx
import httpx
from unittest.mock import patch, MagicMock


ACTIVE_JOB = {
    "job_id": "job-uuid-1",
    "job_hash": "hash-001",
    "apply_url": "https://example.com/jobs/1",
}


def _make_supabase(jobs_data=None):
    """Build a mock Supabase client for check_expiry tests."""
    mock_client = MagicMock()

    jobs_result = MagicMock()
    jobs_result.data = jobs_data if jobs_data is not None else []

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.not_ = select_chain
    select_chain.is_.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = jobs_result

    update_chain = MagicMock()
    update_chain.update.return_value = update_chain
    update_chain.eq.return_value = update_chain
    update_chain.execute.return_value = MagicMock()

    # table() returns select_chain for reads; but update uses the same table object
    # We need a single chain that supports both select and update paths.
    combined_chain = MagicMock()
    combined_chain.select.return_value = combined_chain
    combined_chain.eq.return_value = combined_chain
    combined_chain.not_ = combined_chain
    combined_chain.is_.return_value = combined_chain
    combined_chain.limit.return_value = combined_chain
    combined_chain.execute.return_value = jobs_result
    combined_chain.update.return_value = combined_chain

    mock_client.table.return_value = combined_chain
    return mock_client


@respx.mock
def test_404_response_marks_expired():
    """A 404 response for an apply_url marks the job as expired."""
    respx.head("https://example.com/jobs/1").mock(return_value=httpx.Response(404))

    db = _make_supabase(jobs_data=[ACTIVE_JOB])

    with patch("check_expiry.get_supabase", return_value=db):
        import check_expiry
        result = check_expiry.handler({}, None)

    assert result["expired"] == 1
    assert result["checked"] == 1
    # Confirm update() was called with is_expired=True
    db.table.return_value.update.assert_called_once_with({"is_expired": True})


@respx.mock
def test_200_response_does_not_mark_expired():
    """A 200 response means the job is still active — not marked expired."""
    respx.head("https://example.com/jobs/1").mock(return_value=httpx.Response(200))

    db = _make_supabase(jobs_data=[ACTIVE_JOB])

    with patch("check_expiry.get_supabase", return_value=db):
        import check_expiry
        result = check_expiry.handler({}, None)

    assert result["expired"] == 0
    assert result["checked"] == 1
    db.table.return_value.update.assert_not_called()


@respx.mock
def test_network_error_does_not_mark_expired():
    """A network error (ConnectError) should not mark the job as expired."""
    respx.head("https://example.com/jobs/1").mock(side_effect=httpx.ConnectError("timeout"))

    db = _make_supabase(jobs_data=[ACTIVE_JOB])

    with patch("check_expiry.get_supabase", return_value=db):
        import check_expiry
        result = check_expiry.handler({}, None)

    assert result["expired"] == 0
    assert result["checked"] == 1
    db.table.return_value.update.assert_not_called()
