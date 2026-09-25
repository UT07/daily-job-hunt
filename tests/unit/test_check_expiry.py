"""Unit tests for check_expiry Lambda."""
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
    select_chain.order.return_value = select_chain
    select_chain.range.return_value = select_chain
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
    combined_chain.order.return_value = combined_chain
    combined_chain.range.return_value = combined_chain
    combined_chain.execute.return_value = jobs_result
    combined_chain.update.return_value = combined_chain

    mock_client.table.return_value = combined_chain
    return mock_client


def _make_paginated_supabase(pages):
    """Build a mock Supabase client whose SELECT `.execute()` calls return
    successive pages in order, to exercise check_expiry's `.range()` loop.

    Every test using this helper mocks all HEAD checks as 200 OK, so
    `update()` is never called — every `.execute()` call is consumed by
    the SELECT pagination loop, one per page.
    """
    mock_client = MagicMock()
    combined_chain = MagicMock()
    combined_chain.select.return_value = combined_chain
    combined_chain.eq.return_value = combined_chain
    combined_chain.not_ = combined_chain
    combined_chain.is_.return_value = combined_chain
    combined_chain.order.return_value = combined_chain
    combined_chain.range.return_value = combined_chain
    combined_chain.update.return_value = combined_chain
    combined_chain.execute.side_effect = [MagicMock(data=page) for page in pages]
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


@respx.mock
def test_pagination_walks_multiple_pages(monkeypatch):
    """A result set larger than one page must not be silently truncated.

    Regression test for the unpaginated `.limit(100)` bug (flagged in the
    2026-08-31 audit, still present until this fix): with a page size of 3
    (monkeypatched so the test doesn't need hundreds of fixture rows) and 4
    total matching rows split across 2 pages, the old single-request
    `.limit(100)` code only ever issued one `.execute()` call and would
    have reported 3 jobs checked, never 4 — silently dropping the row that
    fell past the first page.
    """
    import check_expiry
    monkeypatch.setattr(check_expiry, "_PAGE_SIZE", 3)

    page_1 = [
        {"job_id": f"job-{i}", "user_id": "u1", "apply_url": f"https://example.com/p/{i}", "job_hash": f"h{i}"}
        for i in range(3)
    ]
    page_2 = [
        {"job_id": "job-3", "user_id": "u1", "apply_url": "https://example.com/p/3", "job_hash": "h3"},
    ]
    respx.head(url__regex=r"https://example\.com/p/\d").mock(return_value=httpx.Response(200))

    db = _make_paginated_supabase([page_1, page_2])
    with patch("check_expiry.get_supabase", return_value=db):
        result = check_expiry.handler({}, None)

    assert result["checked"] == 4
    # Exactly 2 SELECT pages: 1 would mean pagination never kicked in
    # (the exact regression this test guards against); more would mean the
    # loop doesn't stop once a short page signals the end of the table.
    assert db.table.return_value.execute.call_count == 2


@respx.mock
def test_pagination_stops_after_exact_multiple(monkeypatch):
    """When the row count is an exact multiple of the page size, one more
    (empty) page must still be fetched to confirm the table is exhausted —
    `.range()` gives no other signal that there isn't a page 3."""
    import check_expiry
    monkeypatch.setattr(check_expiry, "_PAGE_SIZE", 2)

    page_1 = [
        {"job_id": "job-0", "user_id": "u1", "apply_url": "https://example.com/q/0", "job_hash": "h0"},
        {"job_id": "job-1", "user_id": "u1", "apply_url": "https://example.com/q/1", "job_hash": "h1"},
    ]
    page_2 = []
    respx.head(url__regex=r"https://example\.com/q/\d").mock(return_value=httpx.Response(200))

    db = _make_paginated_supabase([page_1, page_2])
    with patch("check_expiry.get_supabase", return_value=db):
        result = check_expiry.handler({}, None)

    assert result["checked"] == 2
    assert db.table.return_value.execute.call_count == 2


def test_single_short_page_makes_one_call():
    """The common case (fewer active jobs than one page) must not issue a
    wasted second request — a short page already proves the table ended."""
    import check_expiry

    db = _make_supabase(jobs_data=[ACTIVE_JOB])
    jobs = check_expiry._fetch_active_jobs_with_apply_url(db)

    assert jobs == [ACTIVE_JOB]
    assert db.table.return_value.execute.call_count == 1
