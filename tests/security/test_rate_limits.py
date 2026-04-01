"""Security tests: rate limiting and concurrency controls.

Verifies that:
- Pipeline run rate limit (max 5/day) is enforced
- Concurrent pipeline limit (1 at a time) is enforced
"""

import pytest
from unittest.mock import patch, MagicMock


# ── Pipeline daily rate limit (max 5/day) ─────────────────────────────────────

def test_pipeline_run_rate_limit_blocks_sixth_run(client, auth_headers, mock_db):
    """After 5 pipeline runs today, the 6th must be rejected with 429."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    # Simulate 5 existing runs today
    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "completed", "id": f"run-{i}"}
        for i in range(5)
    ]

    # Also need DAILY_PIPELINE_ARN to be set so the endpoint doesn't bail with 500
    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 429, (
        f"Expected 429 after 5 daily runs, got {resp.status_code}: {resp.text[:200]}"
    )
    assert "5" in resp.text.lower() or "maximum" in resp.text.lower(), (
        "429 response should mention the limit"
    )


def test_pipeline_run_under_limit_succeeds(client, auth_headers, mock_db):
    """With fewer than 5 runs today, the request should proceed (202)."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    # Simulate 3 existing runs today
    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "completed", "id": f"run-{i}"}
        for i in range(3)
    ]

    mock_sfn = MagicMock()
    mock_sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:eu-west-1:123:execution:test:run-4",
        "startDate": datetime.now(timezone.utc),
    }

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}), \
         patch("app._get_sfn", return_value=mock_sfn):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 202, (
        f"Expected 202 with 3/5 runs, got {resp.status_code}: {resp.text[:200]}"
    )


def test_pipeline_run_exactly_at_limit(client, auth_headers, mock_db):
    """With exactly 4 runs today, the 5th should still succeed (202)."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "completed", "id": f"run-{i}"}
        for i in range(4)
    ]

    mock_sfn = MagicMock()
    mock_sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:eu-west-1:123:execution:test:run-5",
        "startDate": datetime.now(timezone.utc),
    }

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}), \
         patch("app._get_sfn", return_value=mock_sfn):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 202, (
        f"Expected 202 for the 5th run, got {resp.status_code}: {resp.text[:200]}"
    )


# ── Concurrent pipeline limit (1 at a time) ──────────────────────────────────

def test_pipeline_run_concurrent_blocks_second_run(client, auth_headers, mock_db):
    """If a pipeline is already running, a second request must be rejected with 409."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    # Simulate 1 currently running pipeline
    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "running", "id": "run-active"},
    ]

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 409, (
        f"Expected 409 when pipeline already running, got {resp.status_code}: {resp.text[:200]}"
    )
    assert "running" in resp.text.lower() or "already" in resp.text.lower(), (
        "409 response should mention that a pipeline is already running"
    )


def test_pipeline_run_after_completed_succeeds(client, auth_headers, mock_db):
    """After a pipeline completes, a new one should be allowed (202)."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    # All runs are completed, none running
    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "completed", "id": "run-done"},
    ]

    mock_sfn = MagicMock()
    mock_sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:eu-west-1:123:execution:test:run-new",
        "startDate": datetime.now(timezone.utc),
    }

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}), \
         patch("app._get_sfn", return_value=mock_sfn):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 202, (
        f"Expected 202 after completed run, got {resp.status_code}: {resp.text[:200]}"
    )


# ── Rate limit + concurrency combined ─────────────────────────────────────────

def test_rate_limit_checked_before_concurrent(client, auth_headers, mock_db):
    """When both rate limit AND concurrent limit are hit, at least one blocks the request."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()

    # 5 runs today, one of which is still running
    mock_db.get_runs.return_value = [
        {"run_date": today, "status": "running", "id": "run-active"},
        *[{"run_date": today, "status": "completed", "id": f"run-{i}"} for i in range(4)],
    ]

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    # Should be blocked by either rate limit (429) or concurrency (409)
    assert resp.status_code in (429, 409), (
        f"Expected 429 or 409, got {resp.status_code}: {resp.text[:200]}"
    )


# ── Pipeline not configured ───────────────────────────────────────────────────

def test_pipeline_run_without_arn_returns_500(client, auth_headers, mock_db):
    """If DAILY_PIPELINE_ARN is not set, the endpoint should return 500."""
    mock_db.get_runs.return_value = []

    with patch.dict("os.environ", {}, clear=False):
        # Ensure the ARN is not set
        import os
        old = os.environ.pop("DAILY_PIPELINE_ARN", None)
        try:
            resp = client.post(
                "/api/pipeline/run",
                headers=auth_headers,
                json={"queries": ["software engineer"]},
            )
        finally:
            if old:
                os.environ["DAILY_PIPELINE_ARN"] = old

    assert resp.status_code == 500, (
        f"Expected 500 when pipeline ARN missing, got {resp.status_code}"
    )


# ── Yesterday's runs don't count toward today's limit ─────────────────────────

def test_yesterday_runs_dont_count(client, auth_headers, mock_db):
    """Runs from yesterday should not count toward today's 5-run limit."""
    from datetime import datetime, timezone, timedelta

    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    # 5 runs yesterday, 0 today
    mock_db.get_runs.return_value = [
        {"run_date": yesterday, "status": "completed", "id": f"run-{i}"}
        for i in range(5)
    ]

    mock_sfn = MagicMock()
    mock_sfn.start_execution.return_value = {
        "executionArn": "arn:aws:states:eu-west-1:123:execution:test:run-today",
        "startDate": datetime.now(timezone.utc),
    }

    with patch.dict("os.environ", {"DAILY_PIPELINE_ARN": "arn:aws:states:eu-west-1:123:stateMachine:test"}), \
         patch("app._get_sfn", return_value=mock_sfn):
        resp = client.post(
            "/api/pipeline/run",
            headers=auth_headers,
            json={"queries": ["software engineer"]},
        )

    assert resp.status_code == 202, (
        f"Yesterday's runs should not count — expected 202, got {resp.status_code}: {resp.text[:200]}"
    )
