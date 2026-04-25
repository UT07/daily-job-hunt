"""Unit tests for shared.load_job."""
from unittest.mock import MagicMock


def _mock_db(row: dict | None):
    db = MagicMock()
    table = MagicMock()
    db.client.table.return_value = table
    chain = MagicMock()
    table.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    result = MagicMock()
    result.data = row
    chain.execute.return_value = result
    return db, table, chain


def test_load_job_returns_row_when_present():
    from shared.load_job import load_job
    row = {"job_id": "j1", "user_id": "u1"}
    db, _, _ = _mock_db(row)
    assert load_job("j1", "u1", db=db) == row


def test_load_job_returns_none_when_missing():
    from shared.load_job import load_job
    db, _, _ = _mock_db(None)
    assert load_job("j1", "u1", db=db) is None


def test_load_job_filters_by_user_id_for_rls():
    from shared.load_job import load_job
    db, _, chain = _mock_db({"job_id": "j1"})
    load_job("j1", "u1", db=db)
    eq_calls = chain.eq.call_args_list
    assert len(eq_calls) == 2
    args = [c.args for c in eq_calls]
    assert ("job_id", "j1") in args
    assert ("user_id", "u1") in args
