"""Tests for config.feature_flags.

The PostHog client is stubbed via set_client() — no network calls.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from config import feature_flags
from config.feature_flags import flag_gated, is_enabled, set_client


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the client + env override after each test."""
    set_client(None)
    os.environ.pop("FEATURE_FLAGS_FORCE", None)
    yield
    set_client(None)
    os.environ.pop("FEATURE_FLAGS_FORCE", None)


def test_no_client_returns_default():
    assert is_enabled("auto_apply", "user-1", default=False) is False
    assert is_enabled("auto_apply", "user-1", default=True) is True


def test_no_user_id_returns_default():
    set_client(MagicMock(feature_enabled=MagicMock(return_value=True)))
    assert is_enabled("auto_apply", None, default=False) is False


def test_client_returns_true():
    client = MagicMock()
    client.feature_enabled.return_value = True
    set_client(client)
    assert is_enabled("auto_apply", "user-1") is True
    client.feature_enabled.assert_called_once_with("auto_apply", "user-1")


def test_client_returns_false_with_default_true_still_false():
    """Explicit False from PostHog overrides default — flag was evaluated."""
    client = MagicMock()
    client.feature_enabled.return_value = False
    set_client(client)
    assert is_enabled("auto_apply", "user-1", default=True) is False


def test_client_returns_none_falls_back_to_default():
    """PostHog returns None when flag key is unknown."""
    client = MagicMock()
    client.feature_enabled.return_value = None
    set_client(client)
    assert is_enabled("typo_flag", "user-1", default=False) is False
    assert is_enabled("typo_flag", "user-1", default=True) is True


def test_client_raises_falls_back_to_default():
    """Network errors must never propagate to user code."""
    client = MagicMock()
    client.feature_enabled.side_effect = ConnectionError("posthog down")
    set_client(client)
    assert is_enabled("auto_apply", "user-1", default=False) is False


def test_force_env_override():
    """FEATURE_FLAGS_FORCE turns on flags regardless of client state."""
    os.environ["FEATURE_FLAGS_FORCE"] = "auto_apply,council_scoring"
    # No client at all
    assert is_enabled("auto_apply", "user-1") is True
    assert is_enabled("council_scoring", "user-1") is True
    assert is_enabled("not_forced", "user-1") is False


def test_decorator_passes_through_when_enabled():
    client = MagicMock()
    client.feature_enabled.return_value = True
    set_client(client)

    user = MagicMock(id="user-1")

    @flag_gated("auto_apply")
    def handler(user):
        return "ok"

    assert handler(user=user) == "ok"


def test_decorator_blocks_with_503_when_disabled():
    client = MagicMock()
    client.feature_enabled.return_value = False
    set_client(client)

    user = MagicMock(id="user-1")

    @flag_gated("auto_apply")
    def handler(user):
        return "ok"

    with pytest.raises(HTTPException) as exc_info:
        handler(user=user)
    assert exc_info.value.status_code == 503
    assert "auto_apply" in exc_info.value.detail


def test_decorator_blocks_when_user_missing():
    """No user in kwargs = treat as anonymous = default."""
    @flag_gated("auto_apply")
    def handler(user=None):
        return "ok"

    with pytest.raises(HTTPException) as exc_info:
        handler()
    assert exc_info.value.status_code == 503
