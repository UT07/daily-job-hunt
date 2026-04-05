"""Tests for seen_jobs Supabase persistence functions."""
from unittest.mock import MagicMock


def test_check_seen_job_found():
    """check_seen_job returns the record when found in Supabase."""
    mock_db = MagicMock()
    mock_db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"canonical_hash": "abc123", "first_seen": "2026-04-01", "score": 75}
    ]
    from main import check_seen_job
    result = check_seen_job(mock_db, "test-user", "abc123")
    assert result is not None
    assert result["score"] == 75
    assert result["canonical_hash"] == "abc123"


def test_check_seen_job_not_found():
    """check_seen_job returns None when no matching record."""
    mock_db = MagicMock()
    mock_db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    from main import check_seen_job
    assert check_seen_job(mock_db, "test-user", "xyz") is None


def test_upsert_seen_job():
    """upsert_seen_job calls Supabase upsert with correct fields."""
    mock_db = MagicMock()
    from main import upsert_seen_job
    upsert_seen_job(
        mock_db, "test-user",
        {"id": "j1", "title": "Eng", "company": "Co", "match_score": 80},
        "abc123",
    )
    mock_db.client.table.assert_called_with("seen_jobs")
    mock_db.client.table.return_value.upsert.assert_called_once()
    call_args = mock_db.client.table.return_value.upsert.call_args
    payload = call_args[0][0]
    assert payload["user_id"] == "test-user"
    assert payload["canonical_hash"] == "abc123"
    assert payload["job_id"] == "j1"
    assert payload["title"] == "Eng"
    assert payload["company"] == "Co"
    assert payload["score"] == 80
    assert payload["matched"] is True


def test_upsert_seen_job_zero_score():
    """upsert_seen_job handles zero match_score gracefully."""
    mock_db = MagicMock()
    from main import upsert_seen_job
    upsert_seen_job(
        mock_db, "test-user",
        {"id": "j2", "title": "Dev", "company": "Acme", "match_score": 0},
        "def456",
    )
    call_args = mock_db.client.table.return_value.upsert.call_args
    payload = call_args[0][0]
    assert payload["score"] == 0
    assert payload["matched"] is False


def test_upsert_seen_job_none_score():
    """upsert_seen_job handles None match_score without error."""
    mock_db = MagicMock()
    from main import upsert_seen_job
    upsert_seen_job(
        mock_db, "test-user",
        {"id": "j3", "title": "PM", "company": "Biz"},
        "ghi789",
    )
    call_args = mock_db.client.table.return_value.upsert.call_args
    payload = call_args[0][0]
    assert payload["score"] == 0
    assert payload["matched"] is False


def test_upsert_seen_job_uses_job_id_fallback():
    """upsert_seen_job falls back to job_id key when id is absent."""
    mock_db = MagicMock()
    from main import upsert_seen_job
    upsert_seen_job(
        mock_db, "test-user",
        {"job_id": "fallback-id", "title": "QA", "company": "Test"},
        "jkl012",
    )
    call_args = mock_db.client.table.return_value.upsert.call_args
    payload = call_args[0][0]
    assert payload["job_id"] == "fallback-id"


def test_check_seen_job_calls_correct_table():
    """check_seen_job queries the seen_jobs table with correct filters."""
    mock_db = MagicMock()
    mock_db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    from main import check_seen_job
    check_seen_job(mock_db, "user-abc", "hash-xyz")
    mock_db.client.table.assert_called_with("seen_jobs")
