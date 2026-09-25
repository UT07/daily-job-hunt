"""Unit tests for lambdas/pipeline/ai_helper.py."""
import contextlib
import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ai_helper import ai_complete, ai_complete_cached, _build_provider_list

# Also import ai_client from project root for provider-class tests
_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ---------------------------------------------------------------------------
# DeepSeek removal — the provider should not exist anywhere in the codebase
# ---------------------------------------------------------------------------

class TestDeepSeekRemoved:
    """Verify DeepSeek has been fully removed from the provider chain."""

    def test_deepseek_provider_class_not_in_ai_client(self):
        """DeepSeekProvider class must not exist in ai_client module."""
        import ai_client as mod
        provider_classes = [
            name for name in dir(mod)
            if name.endswith("Provider") and name != "AIProvider"
        ]
        assert "DeepSeekProvider" not in provider_classes

    def test_deepseek_not_in_lambda_ai_helper_providers(self):
        """DeepSeek must not appear as a provider in the ai_helper failover chain."""
        # Inspect the source of ai_complete — check that no provider dict
        # has name="deepseek" or key_param referencing DEEPSEEK.
        # Comments explaining the removal are allowed.
        import inspect
        source = inspect.getsource(ai_complete)
        # Check for provider dict entries (the actual provider configuration)
        assert '"name": "deepseek"' not in source, (
            "ai_helper.ai_complete still has deepseek as a provider"
        )
        assert "DEEPSEEK_API_KEY" not in source, (
            "ai_helper.ai_complete still references DEEPSEEK_API_KEY"
        )


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

    def setup_method(self):
        """Disable A/B provider shuffle for deterministic tests."""
        self._random_patcher = patch("ai_helper.random.random", return_value=1.0)
        self._random_patcher.start()

    def teardown_method(self):
        self._random_patcher.stop()

    def test_first_provider_succeeds(self):
        """When the first provider returns 200, its response is returned immediately."""
        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", return_value=_make_ok_response("Hello!")):
            result = ai_complete("Say hello")

        # Derive the expectation from the configured council rather than
        # hardcoding a model id. Pinning the id here is what let the whole
        # council rot: the models were retired by their vendors but this
        # assertion kept passing, so CI reported green while production
        # scored nothing. Model *identity* is guarded by
        # tests/unit/test_ai_council_models.py; this test guards *behaviour*.
        first = _build_provider_list()[0]
        assert result == {
            "content": "Hello!",
            "provider": first["name"],
            "model": first["model"],
        }

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

        assert result["content"] == "From provider 2"
        # Second provider in the configured council — again derived, not pinned.
        assert result["provider"] == _build_provider_list()[1]["name"]
        assert call_count == 2

    def test_tries_next_provider_when_first_rate_limited(self):
        """When the first provider returns 429, the second provider is tried."""
        rate_limit_resp = _make_error_response(429)
        ok_resp = _make_ok_response("From provider 2")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=[rate_limit_resp, ok_resp]):
            result = ai_complete("prompt")

        assert result["content"] == "From provider 2"

    def test_tries_next_provider_when_first_returns_500(self):
        """When the first provider returns a 5xx error, the second provider is tried."""
        error_resp = _make_error_response(500)
        ok_resp = _make_ok_response("Recovered")

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=[error_resp, ok_resp]):
            result = ai_complete("prompt")

        assert result["content"] == "Recovered"

    def test_raises_runtime_error_when_all_providers_fail(self):
        """When every provider fails, a RuntimeError is raised."""
        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", side_effect=httpx.ConnectError("All down")):
            with pytest.raises(RuntimeError, match="All \\d+ AI providers failed"):
                ai_complete("prompt")

    def test_raises_runtime_error_when_all_providers_rate_limited(self):
        """When every provider returns 429, a RuntimeError is raised."""
        rate_limit_resp = _make_error_response(429)

        with patch("ai_helper.get_param", return_value="real-api-key"), \
             patch("httpx.post", return_value=rate_limit_resp):
            with pytest.raises(RuntimeError, match="All \\d+ AI providers failed"):
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
            data=[{"response": "cached answer", "provider": "cache", "model": "cache"}]
        )
        mock_db.table.return_value = mock_table

        with patch("ai_helper.get_supabase", return_value=mock_db), \
             patch("ai_helper.ai_complete") as mock_ai:
            result = ai_complete_cached("hello", system="sys")

        assert result == {"content": "cached answer", "provider": "cache", "model": "cache"}
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
             patch("ai_helper.ai_complete", return_value={"content": "fresh answer", "provider": "p1", "model": "m1"}) as mock_ai:
            result = ai_complete_cached("hello", system="sys")

        assert result == {"content": "fresh answer", "provider": "p1", "model": "m1"}
        mock_ai.assert_called_once_with("hello", "sys", temperature=0.3, max_tokens=4096)

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
             patch("ai_helper.ai_complete", return_value={"content": "new response", "provider": "p1", "model": "m1"}):
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
             patch("ai_helper.ai_complete", return_value={"content": "response", "provider": "p1", "model": "m1"}):
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
             patch("ai_helper.ai_complete", return_value={"content": "r", "provider": "p1", "model": "m1"}):
            before = datetime.utcnow()
            ai_complete_cached("prompt", cache_hours=48)
            after = datetime.utcnow()

        upsert_payload = mock_table.upsert.call_args[0][0]
        expires_at = datetime.fromisoformat(upsert_payload["expires_at"])
        expected_min = before + timedelta(hours=48)
        expected_max = after + timedelta(hours=48)
        assert expected_min <= expires_at <= expected_max


# ---------------------------------------------------------------------------
# Lazy SSM client init (caught 2026-04-29 by Deploy Readiness CI gate)
# ---------------------------------------------------------------------------

class TestLazySsmInit:
    """Module-level boto3.client('ssm') was forcing AWS_DEFAULT_REGION on every
    importer including the new docker-run import smoke. Lazy-init defers it."""

    def test_module_does_not_create_ssm_at_import(self):
        # Reload the module fresh; the only side effect must be that _ssm is None.
        import importlib
        import ai_helper
        importlib.reload(ai_helper)
        assert ai_helper._ssm is None, "module-level ssm should not be eagerly created"

    def test_get_param_creates_ssm_lazily(self):
        import importlib
        import ai_helper
        importlib.reload(ai_helper)
        with patch("ai_helper.boto3.client") as mock_client:
            mock_client.return_value.get_parameter.return_value = {
                "Parameter": {"Value": "secret"}
            }
            ai_helper.get_param("/foo")
            assert mock_client.call_count == 1
            # Second call reuses the same client (no second create). This must be
            # a DIFFERENT parameter name: get_param memoizes per name, so asking
            # for "/foo" again would be served from _param_cache without ever
            # reaching _get_ssm(), and this assertion would pass vacuously.
            ai_helper.get_param("/bar")
            assert mock_client.call_count == 1, "ssm client should be reused, not recreated"


# ---------------------------------------------------------------------------
# get_param / get_supabase memoization
#
# Both helpers used to redo their full work on every call: get_param() a live
# SSM GetParameter round trip with KMS decryption, get_supabase() a fresh
# create_client() on top of two of those. Since nothing in the pipeline calls
# them once (get_supabase() runs per cache read AND per cache write,
# _call_provider() reads a key per provider hop), one logical step paid for the
# same handful of immutable values several times over. These tests pin the
# memoization down by call count, which is the only thing that actually
# regressed when it was missing.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _fresh_ssm(values):
    """Point ai_helper at a mock SSM serving `values`, from a cold cache.

    Clears the param/client caches AND the lazy boto3 handle on both entry and
    exit: leaving _ssm set would make `patch("ai_helper.boto3.client")` a no-op
    for the next test, since _get_ssm() short-circuits on an existing client.
    """
    import ai_helper
    ai_helper.reset_caches()
    ai_helper._ssm = None

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = (
        lambda Name, WithDecryption=False: {"Parameter": {"Value": values[Name]}}
    )
    try:
        with patch("ai_helper.boto3.client", return_value=mock_ssm) as mock_client:
            yield mock_ssm, mock_client
    finally:
        ai_helper.reset_caches()
        ai_helper._ssm = None


class TestGetParamCaching:
    """get_param memoizes per parameter name for the life of the process."""

    def test_repeated_reads_of_one_name_hit_ssm_once(self):
        import ai_helper
        with _fresh_ssm({"/naukribaba/GROQ_API_KEY": "gk"}) as (mock_ssm, _):
            for _ in range(5):
                assert ai_helper.get_param("/naukribaba/GROQ_API_KEY") == "gk"
            assert mock_ssm.get_parameter.call_count == 1

    def test_each_distinct_name_is_fetched_exactly_once(self):
        import ai_helper
        values = {"/a": "1", "/b": "2", "/c": "3"}
        with _fresh_ssm(values) as (mock_ssm, _):
            for _ in range(3):
                assert [ai_helper.get_param(n) for n in ("/a", "/b", "/c")] == ["1", "2", "3"]
            assert mock_ssm.get_parameter.call_count == 3

    def test_decryption_still_requested_on_the_real_fetch(self):
        """Memoizing must not quietly drop WithDecryption — these are SecureStrings."""
        import ai_helper
        with _fresh_ssm({"/secret": "plaintext"}) as (mock_ssm, _):
            ai_helper.get_param("/secret")
            mock_ssm.get_parameter.assert_called_once_with(
                Name="/secret", WithDecryption=True
            )

    def test_empty_string_value_is_cached_not_refetched(self):
        """A parameter that is legitimately "" must not re-hit SSM forever.

        This is why get_param tests membership rather than truthiness — a
        `if not _param_cache.get(name)` guard would miss on every call here.
        """
        import ai_helper
        with _fresh_ssm({"/empty": ""}) as (mock_ssm, _):
            assert ai_helper.get_param("/empty") == ""
            assert ai_helper.get_param("/empty") == ""
            assert mock_ssm.get_parameter.call_count == 1

    def test_failed_fetch_is_not_cached(self):
        """A transient SSM error must not poison the cache for the process."""
        import ai_helper
        ai_helper.reset_caches()
        ai_helper._ssm = None
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.side_effect = [
            RuntimeError("throttled"),
            {"Parameter": {"Value": "eventually"}},
        ]
        try:
            with patch("ai_helper.boto3.client", return_value=mock_ssm):
                with pytest.raises(RuntimeError, match="throttled"):
                    ai_helper.get_param("/flaky")
                # Retry must reach SSM again rather than serve a cached miss.
                assert ai_helper.get_param("/flaky") == "eventually"
                assert mock_ssm.get_parameter.call_count == 2
        finally:
            ai_helper.reset_caches()
            ai_helper._ssm = None


class TestGetSupabaseCaching:
    """get_supabase constructs one client per process, not one per call."""

    _PARAMS = {
        "/naukribaba/SUPABASE_URL": "https://test.supabase.co",
        "/naukribaba/SUPABASE_SERVICE_KEY": "test-key",
    }

    def test_client_constructed_once_and_same_instance_returned(self):
        import ai_helper
        sentinel = MagicMock(name="supabase_client")
        with _fresh_ssm(self._PARAMS):
            with patch("supabase.create_client", return_value=sentinel) as mock_create:
                first = ai_helper.get_supabase()
                second = ai_helper.get_supabase()
        assert first is sentinel and second is sentinel
        assert mock_create.call_count == 1

    def test_repeated_calls_do_not_re_read_ssm(self):
        """The motivating case: 10 get_supabase() calls used to be 20 SSM reads."""
        import ai_helper
        with _fresh_ssm(self._PARAMS) as (mock_ssm, _):
            with patch("supabase.create_client", return_value=MagicMock()):
                for _ in range(10):
                    ai_helper.get_supabase()
            # Exactly one read of URL + one of the service key, ever.
            assert mock_ssm.get_parameter.call_count == 2

    def test_client_built_from_the_configured_url_and_key(self):
        import ai_helper
        with _fresh_ssm(self._PARAMS):
            with patch("supabase.create_client", return_value=MagicMock()) as mock_create:
                ai_helper.get_supabase()
        mock_create.assert_called_once_with("https://test.supabase.co", "test-key")

    def test_construction_failure_is_not_cached(self):
        """A failed create_client must leave the next call free to retry."""
        import ai_helper
        good = MagicMock(name="supabase_client")
        with _fresh_ssm(self._PARAMS):
            with patch("supabase.create_client",
                       side_effect=[RuntimeError("dns"), good]) as mock_create:
                with pytest.raises(RuntimeError, match="dns"):
                    ai_helper.get_supabase()
                assert ai_helper.get_supabase() is good
        assert mock_create.call_count == 2


class TestResetCaches:
    """reset_caches() is the escape hatch for rotated params / per-call tests."""

    def test_reset_forces_a_fresh_param_read(self):
        import ai_helper
        with _fresh_ssm({"/rotating": "old"}) as (mock_ssm, _):
            assert ai_helper.get_param("/rotating") == "old"
            mock_ssm.get_parameter.side_effect = (
                lambda Name, WithDecryption=False: {"Parameter": {"Value": "new"}}
            )
            # Still cached — that is the point of the cache.
            assert ai_helper.get_param("/rotating") == "old"
            ai_helper.reset_caches()
            assert ai_helper.get_param("/rotating") == "new"

    def test_reset_forces_a_fresh_client(self):
        import ai_helper
        first, second = MagicMock(name="c1"), MagicMock(name="c2")
        with _fresh_ssm(TestGetSupabaseCaching._PARAMS):
            with patch("supabase.create_client", side_effect=[first, second]):
                assert ai_helper.get_supabase() is first
                ai_helper.reset_caches()
                assert ai_helper.get_supabase() is second

    def test_reset_leaves_the_boto3_client_alone(self):
        """_ssm is a connection holder, not a cached value — recreating it buys
        nothing, and the lazy-init contract above is about import time only."""
        import ai_helper
        with _fresh_ssm({"/x": "y"}):
            ai_helper.get_param("/x")
            assert ai_helper._ssm is not None
            ai_helper.reset_caches()
            assert ai_helper._ssm is not None


class TestCachedCallRoundTrips:
    """End-to-end: what a single ai_complete_cached() now costs in round trips."""

    def _db_with_hit(self):
        db = MagicMock()
        table = MagicMock()
        table.select.return_value = table
        table.eq.return_value = table
        table.gte.return_value = table
        table.execute.return_value = MagicMock(
            data=[{"response": "cached", "provider": "p", "model": "m"}]
        )
        db.table.return_value = table
        return db

    def test_cache_hit_costs_two_ssm_reads_and_one_client(self):
        import ai_helper
        with _fresh_ssm(TestGetSupabaseCaching._PARAMS) as (mock_ssm, _):
            with patch("supabase.create_client", return_value=self._db_with_hit()) as mock_create:
                result = ai_helper.ai_complete_cached("prompt", system="sys")
                assert result["content"] == "cached"
                assert mock_ssm.get_parameter.call_count == 2
                assert mock_create.call_count == 1

                # A second call — the case that used to double everything —
                # adds no SSM traffic and no second client.
                ai_helper.ai_complete_cached("another prompt", system="sys")
                assert mock_ssm.get_parameter.call_count == 2
                assert mock_create.call_count == 1

    def test_cache_miss_reuses_the_client_across_read_and_write(self):
        """The miss path touches the db twice (select, then upsert). Both must
        come from the same client.

        Not a regression guard for the caching work — ai_complete_cached already
        bound get_supabase() to a local and reused it, so this held before the
        fix too. It pins that down so a future refactor can't turn the write leg
        into a second get_supabase() call and quietly reintroduce the cost.
        """
        import ai_helper
        db = MagicMock()
        table = MagicMock()
        table.select.return_value = table
        table.eq.return_value = table
        table.gte.return_value = table
        table.execute.return_value = MagicMock(data=[])
        table.upsert.return_value = table
        db.table.return_value = table

        with _fresh_ssm(TestGetSupabaseCaching._PARAMS) as (mock_ssm, _):
            with patch("supabase.create_client", return_value=db) as mock_create, \
                 patch("ai_helper.ai_complete",
                       return_value={"content": "fresh", "provider": "p", "model": "m"}):
                ai_helper.ai_complete_cached("prompt", system="sys")

        table.upsert.assert_called_once()
        assert mock_create.call_count == 1
        assert mock_ssm.get_parameter.call_count == 2

    def test_provider_keys_are_read_once_across_the_failover_chain(self):
        """Every Groq provider shares one key_param — the chain used to re-read
        it (and pay KMS) on each hop."""
        import ai_helper
        keyed = {p["key_param"] for p in ai_helper._build_provider_list()}
        with _fresh_ssm({k: "real-api-key" for k in keyed}) as (mock_ssm, _):
            with patch("ai_helper.random.random", return_value=1.0), \
                 patch("httpx.post", side_effect=httpx.ConnectError("down")):
                with pytest.raises(RuntimeError, match="All \\d+ AI providers failed"):
                    ai_helper.ai_complete("prompt")
            # One read per DISTINCT key param, not one per provider hop.
            assert mock_ssm.get_parameter.call_count == len(keyed)
            assert len(keyed) < len(ai_helper._build_provider_list())
