"""Unit tests for lambdas/pipeline/ai_helper.py."""
import hashlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ai_helper import ai_complete, ai_complete_cached


def _make_ok_response(content: str) -> MagicMock:
    """Build a mock httpx.Response that looks like a successful AI API response."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock_resp


def _make_error_response(status_code: int) -> MagicMock:
    """Build a mock httpx.Response with a non-200 status."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    return mock_resp


# ---------------------------------------------------------------------------
# ai_complete — failover chain tests
# ---------------------------------------------------------------------------

class TestAiComplete:
    """Tests for the ai_complete failover chain."""

    def test_first_provider_succeeds(self):
        """When the first provider returns 200, its response is returned immediately."""
        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", return_value=_make_ok_response("Hello!")):
            result = ai_complete("Say hello")

        assert result == "Hello!"

    def test_tries_next_provider_when_first_fails_with_exception(self):
        """When the first provider raises an exception, the second provider is tried."""
        ok_response = _make_ok_response("From provider 2")

        call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            return ok_response

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=post_side_effect):
            result = ai_complete("prompt")

        assert result == "From provider 2"
        assert call_count == 2

    def test_tries_next_provider_when_first_rate_limited(self):
        """When the first provider returns 429, the second provider is tried."""
        rate_limit_resp = _make_error_response(429)
        ok_resp = _make_ok_response("From provider 2")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=[rate_limit_resp, ok_resp]):
            result = ai_complete("prompt")

        assert result == "From provider 2"

    def test_tries_next_provider_when_first_returns_500(self):
        """When the first provider returns a 5xx error, the second provider is tried."""
        error_resp = _make_error_response(500)
        ok_resp = _make_ok_response("Recovered")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=[error_resp, ok_resp]):
            result = ai_complete("prompt")

        assert result == "Recovered"

    def test_raises_runtime_error_when_all_providers_fail(self):
        """When every provider fails, a RuntimeError is raised."""
        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=httpx.ConnectError("All down")):
            with pytest.raises(RuntimeError, match="All 5 AI providers failed"):
                ai_complete("prompt")

    def test_raises_runtime_error_when_all_providers_rate_limited(self):
        """When every provider returns 429, a RuntimeError is raised."""
        rate_limit_resp = _make_error_response(429)

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", return_value=rate_limit_resp):
            with pytest.raises(RuntimeError, match="All 5 AI providers failed"):
                ai_complete("prompt")

    def test_skips_providers_with_mock_value_key(self):
        """Providers whose API key is 'mock-value' are skipped silently."""
        # get_param returns "mock-value" for all providers — all are skipped.
        with patch("ai_helper.get_param", return_value="mock-value"), \
             patch("httpx.post") as mock_post:
            with pytest.raises(RuntimeError):
                ai_complete("prompt")
        # httpx.post should never have been called since all keys were "mock-value"
        mock_post.assert_not_called()

    def test_system_prompt_included_in_messages(self):
        """When a system prompt is provided, it appears as the first message."""
        captured_calls = []

        def capture_post(*args, **kwargs):
            captured_calls.append(kwargs.get("json", {}))
            return _make_ok_response("ok")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=capture_post):
            ai_complete("user prompt", system="you are helpful")

        messages = captured_calls[0]["messages"]
        assert messages[0] == {"role": "system", "content": "you are helpful"}
        assert messages[1] == {"role": "user", "content": "user prompt"}

    def test_no_system_prompt_sends_only_user_message(self):
        """When system prompt is empty, only the user message is sent."""
        captured_calls = []

        def capture_post(*args, **kwargs):
            captured_calls.append(kwargs.get("json", {}))
            return _make_ok_response("ok")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=capture_post):
            ai_complete("just a user prompt")

        messages = captured_calls[0]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_max_tokens_passed_to_provider(self):
        """The max_tokens parameter is forwarded to the AI provider."""
        captured = []

        def capture_post(*args, **kwargs):
            captured.append(kwargs.get("json", {}))
            return _make_ok_response("ok")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=capture_post):
            ai_complete("prompt", max_tokens=1024)

        assert captured[0]["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# ai_complete_cached — cache hit / miss tests
# ---------------------------------------------------------------------------

class TestAiCompleteCached:
    """Tests for the ai_complete_cached Supabase-backed cache."""

    def _cache_key(self, system: str, prompt: str) -> str:
        return hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()

    def test_returns_cached_response_on_hit(self):
        """When the cache contains a valid (non-expired) entry, it is returned
        without calling the AI provider."""
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.execute.return_value = MagicMock(
            data=[{"response": "cached answer"}]
        )
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete") as mock_ai:
            result = ai_complete_cached("hello", system="sys")

        assert result == "cached answer"
        mock_ai.assert_not_called()

    def test_calls_ai_on_cache_miss(self):
        """When the cache returns no data, the AI provider is called."""
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_table.upsert.return_value = mock_table
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete", return_value="fresh answer") as mock_ai:
            result = ai_complete_cached("hello", system="sys")

        assert result == "fresh answer"
        mock_ai.assert_called_once_with("hello", "sys")

    def test_writes_to_cache_on_miss(self):
        """After a cache miss + AI call, the result is upserted into the cache."""
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_table.upsert.return_value = mock_table
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete", return_value="new response"):
            ai_complete_cached("my prompt", system="my system", cache_hours=24)

        # Verify upsert was called with the right cache key and response
        expected_key = self._cache_key("my system", "my prompt")
        upsert_call_args = mock_table.upsert.call_args
        upsert_payload = upsert_call_args[0][0]
        assert upsert_payload["cache_key"] == expected_key
        assert upsert_payload["response"] == "new response"

    def test_cache_key_includes_system_prompt(self):
        """Two calls with different system prompts produce different cache keys."""
        keys_used = []

        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.upsert.return_value = mock_table

        def eq_side_effect(field, value):
            if field == "cache_key":
                keys_used.append(value)
            return mock_table

        mock_table.eq.side_effect = eq_side_effect
        mock_table.execute.return_value = MagicMock(data=[])
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete", return_value="response"):
            ai_complete_cached("same prompt", system="system A")
            ai_complete_cached("same prompt", system="system B")

        assert len(keys_used) == 2
        assert keys_used[0] != keys_used[1]

    def test_cache_expiry_uses_provided_hours(self):
        """The expires_at timestamp uses the cache_hours parameter."""
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_table.upsert.return_value = mock_table
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete", return_value="r"):
            before = datetime.utcnow()
            ai_complete_cached("prompt", cache_hours=48)
            after = datetime.utcnow()

        upsert_payload = mock_table.upsert.call_args[0][0]
        expires_at = datetime.fromisoformat(upsert_payload["expires_at"])
        expected_min = before + timedelta(hours=48)
        expected_max = after + timedelta(hours=48)
        assert expected_min <= expires_at <= expected_max
