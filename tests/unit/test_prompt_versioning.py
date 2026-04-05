"""Tests for prompt versioning CRUD operations."""
from unittest.mock import MagicMock

from utils.prompt_versioning import (
    create_prompt_version,
    load_active_prompt,
    rollback_prompt,
)


def _mock_chain(mock, methods):
    """Build a fluent-API mock chain and return the terminal mock."""
    current = mock
    for method in methods:
        current = getattr(current.return_value, method)
    return current


# ---------------------------------------------------------------------------
# load_active_prompt
# ---------------------------------------------------------------------------


def test_load_active_prompt_returns_latest():
    mock_db = MagicMock()
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = [
        {"id": "p1", "version": 3, "content": "You are a scoring AI...", "active_to": None}
    ]

    prompt = load_active_prompt(mock_db, "test-user", "scoring_system")

    assert prompt is not None
    assert prompt["version"] == 3
    assert prompt["id"] == "p1"
    mock_db.table.assert_called_with("prompt_versions")


def test_load_active_prompt_none_when_empty():
    mock_db = MagicMock()
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = []

    result = load_active_prompt(mock_db, "test-user", "scoring_system")

    assert result is None


# ---------------------------------------------------------------------------
# create_prompt_version
# ---------------------------------------------------------------------------


def test_create_prompt_version_first():
    """First version for a prompt should be version 1."""
    mock_db = MagicMock()
    # load_active_prompt returns nothing
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = []

    version = create_prompt_version(
        mock_db, "test-user", "scoring_system", "New prompt"
    )

    assert version == 1
    mock_db.table.return_value.insert.assert_called_once()
    call_args = mock_db.table.return_value.insert.call_args[0][0]
    assert call_args["version"] == 1
    assert call_args["content"] == "New prompt"
    assert call_args["created_by"] == "manual"


def test_create_prompt_version_increments():
    """New version should be current + 1 and deactivate the old one."""
    mock_db = MagicMock()
    # load_active_prompt returns existing version 2
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = [
        {"id": "p1", "version": 2, "content": "Old prompt", "active_to": None}
    ]

    version = create_prompt_version(
        mock_db, "test-user", "scoring_system", "New prompt"
    )

    assert version == 3
    # Should have called update to deactivate old version
    mock_db.table.return_value.update.assert_called()


def test_create_prompt_version_custom_created_by():
    """created_by should propagate to the insert payload."""
    mock_db = MagicMock()
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = []

    version = create_prompt_version(
        mock_db, "test-user", "scoring_system", "AI prompt", created_by="self_improver"
    )

    assert version == 1
    call_args = mock_db.table.return_value.insert.call_args[0][0]
    assert call_args["created_by"] == "self_improver"


# ---------------------------------------------------------------------------
# rollback_prompt
# ---------------------------------------------------------------------------


def test_rollback_prompt_success():
    """Rollback should deactivate current and reactivate previous."""
    mock_db = MagicMock()
    # load_active_prompt returns version 3
    load_chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    load_chain.return_value.data = [
        {"id": "p3", "version": 3, "content": "V3", "active_to": None}
    ]
    # Previous version lookup (3 eq calls, no is_)
    prev_chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "eq", "execute"],
    )
    prev_chain.return_value.data = [
        {"id": "p2", "version": 2, "content": "V2"}
    ]

    result = rollback_prompt(mock_db, "test-user", "scoring_system")

    assert result is True


def test_rollback_prompt_fails_at_version_1():
    """Cannot rollback when already at version 1."""
    mock_db = MagicMock()
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = [
        {"id": "p1", "version": 1, "content": "V1", "active_to": None}
    ]

    result = rollback_prompt(mock_db, "test-user", "scoring_system")

    assert result is False


def test_rollback_prompt_fails_when_no_active():
    """Cannot rollback when no active prompt exists."""
    mock_db = MagicMock()
    chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    chain.return_value.data = []

    result = rollback_prompt(mock_db, "test-user", "scoring_system")

    assert result is False


def test_rollback_prompt_fails_when_previous_missing():
    """Rollback should return False if previous version is not found in DB."""
    mock_db = MagicMock()
    # load_active_prompt returns version 3
    load_chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "is_", "order", "limit", "execute"],
    )
    load_chain.return_value.data = [
        {"id": "p3", "version": 3, "content": "V3", "active_to": None}
    ]
    # Previous version lookup returns empty
    prev_chain = _mock_chain(
        mock_db.table,
        ["select", "eq", "eq", "eq", "execute"],
    )
    prev_chain.return_value.data = []

    result = rollback_prompt(mock_db, "test-user", "scoring_system")

    # Deactivated current but couldn't find previous -> False
    assert result is False
