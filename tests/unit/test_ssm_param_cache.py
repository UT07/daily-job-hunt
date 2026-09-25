"""The get_param / get_supabase memoization contract, for every module carrying a copy.

`ai_helper` was memoized first (see TestGetParamCaching in test_ai_helper.py,
which also covers the ai_complete_cached / failover round-trip counts specific
to that module). The same helpers are copy-pasted into 20 more Lambda modules,
which cannot share one implementation: template.yaml gives lambdas/pipeline/ and
lambdas/scrapers/ separate CodeUris that SAM flattens into separate /var/task
directories, so a scraper has no ai_helper.py to import. See
lambdas/pipeline/agents/_ai_helper.py for the full import-shape story.

Rather than 21 near-identical test classes, these parametrize over the modules
and assert the contract by round-trip count — the only thing that actually
regressed when the memoization was missing. Every module in MODULES is imported
and checked, so a new copy is covered the moment it appears in the list, and
test_every_copy_is_covered fails if a copy is added and NOT listed.
"""
import contextlib
import importlib
import inspect
import pathlib
import re

import pytest
from unittest.mock import MagicMock

# Modules whose get_param/get_supabase are memoized. tests/conftest.py puts
# both lambdas/pipeline and lambdas/scrapers on sys.path, so these import flat
# exactly as they do inside their deployed, flattened /var/task.
PIPELINE_MODULES = [
    "ai_helper",
    "check_expiry",
    "find_contacts",
    "load_config",
    "merge_dedup",
    "notify_error",
    "save_metrics",
    "send_email",
    "send_followup_reminders",
    "send_stale_nudges",
]
SCRAPER_MODULES = [
    "scrape_adzuna",
    "scrape_apify",
    "scrape_ashby",
    "scrape_contacts",
    "scrape_glassdoor",
    "scrape_greenhouse",
    "scrape_hn",
    "scrape_indeed",
    "scrape_irish",
    "scrape_linkedin",
    "scrape_yc",
]
MODULES = PIPELINE_MODULES + SCRAPER_MODULES

SUPABASE_PARAMS = {
    "/naukribaba/SUPABASE_URL": "https://test.supabase.co",
    "/naukribaba/SUPABASE_SERVICE_KEY": "test-service-key",
}


def _owns_supabase(mod):
    """True if the module builds its own client rather than importing one.

    Keyed on the private `_supabase` global, not on `get_supabase` being
    present: send_followup_reminders and send_stale_nudges import get_supabase
    from ai_helper, so the *name* resolves in their namespace while the cached
    client — and the reset of it — belongs to ai_helper.
    """
    return hasattr(mod, "_supabase")


@contextlib.contextmanager
def _mock_ssm(mod, values):
    """Point one module's SSM handle at a mock serving `values`, cache cold.

    Sets the handle directly instead of patching boto3.client, because the two
    families reach SSM differently: lambdas/pipeline lazily via _get_ssm() off a
    module-level `_ssm`, lambdas/scrapers eagerly off a module-level `ssm` built
    at import. Patching boto3.client would be a no-op for both once the handle
    already exists.
    """
    mod.reset_caches()
    attr = "_ssm" if hasattr(mod, "_ssm") else "ssm"
    original = getattr(mod, attr)

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = (
        lambda Name, WithDecryption=False: {"Parameter": {"Value": values[Name]}}
    )
    setattr(mod, attr, mock_ssm)
    try:
        yield mock_ssm
    finally:
        mod.reset_caches()
        setattr(mod, attr, original)


@pytest.fixture(params=MODULES)
def mod(request):
    """Each memoizing module, imported flat."""
    return importlib.import_module(request.param)


# ---------------------------------------------------------------------------
# get_param
# ---------------------------------------------------------------------------

class TestGetParamCaching:

    def test_repeated_reads_of_one_name_hit_ssm_once(self, mod):
        with _mock_ssm(mod, {"/naukribaba/GROQ_API_KEY": "gk"}) as ssm:
            for _ in range(5):
                assert mod.get_param("/naukribaba/GROQ_API_KEY") == "gk"
            assert ssm.get_parameter.call_count == 1

    def test_each_distinct_name_is_fetched_exactly_once(self, mod):
        with _mock_ssm(mod, {"/a": "1", "/b": "2", "/c": "3"}) as ssm:
            for _ in range(3):
                assert [mod.get_param(n) for n in ("/a", "/b", "/c")] == ["1", "2", "3"]
            assert ssm.get_parameter.call_count == 3

    def test_decryption_still_requested_on_the_real_fetch(self, mod):
        """Memoizing must not quietly drop WithDecryption — these are SecureStrings."""
        with _mock_ssm(mod, {"/secret": "plaintext"}) as ssm:
            mod.get_param("/secret")
            ssm.get_parameter.assert_called_once_with(
                Name="/secret", WithDecryption=True
            )

    def test_empty_string_value_is_cached_not_refetched(self, mod):
        """A parameter that is legitimately "" must not re-hit SSM forever.

        This is why get_param tests membership rather than truthiness — a
        `if not _param_cache.get(name)` guard would miss on every call here.
        """
        with _mock_ssm(mod, {"/empty": ""}) as ssm:
            assert mod.get_param("/empty") == ""
            assert mod.get_param("/empty") == ""
            assert ssm.get_parameter.call_count == 1

    def test_failed_fetch_is_not_cached(self, mod):
        """A transient SSM error must not poison the cache for the process."""
        with _mock_ssm(mod, {}) as ssm:
            ssm.get_parameter.side_effect = [
                RuntimeError("throttled"),
                {"Parameter": {"Value": "eventually"}},
            ]
            with pytest.raises(RuntimeError, match="throttled"):
                mod.get_param("/flaky")
            # The retry must reach SSM again rather than serve a cached miss.
            assert mod.get_param("/flaky") == "eventually"
            assert ssm.get_parameter.call_count == 2


# ---------------------------------------------------------------------------
# get_supabase
# ---------------------------------------------------------------------------

class TestGetSupabaseCaching:

    def test_client_constructed_once_and_same_instance_returned(self, mod, monkeypatch):
        if not _owns_supabase(mod):
            pytest.skip(f"{mod.__name__} imports get_supabase from ai_helper")
        import supabase

        sentinel = MagicMock(name="supabase_client")
        create = MagicMock(return_value=sentinel)
        monkeypatch.setattr(supabase, "create_client", create)

        with _mock_ssm(mod, SUPABASE_PARAMS):
            assert mod.get_supabase() is sentinel
            assert mod.get_supabase() is sentinel
        assert create.call_count == 1

    def test_repeated_calls_do_not_re_read_ssm(self, mod, monkeypatch):
        """The motivating case: 10 get_supabase() calls were 20 SSM reads."""
        if not _owns_supabase(mod):
            pytest.skip(f"{mod.__name__} imports get_supabase from ai_helper")
        import supabase

        monkeypatch.setattr(supabase, "create_client", MagicMock(return_value=MagicMock()))
        with _mock_ssm(mod, SUPABASE_PARAMS) as ssm:
            for _ in range(10):
                mod.get_supabase()
            # One read of the URL + one of the service key, ever.
            assert ssm.get_parameter.call_count == 2

    def test_client_built_from_the_configured_url_and_key(self, mod, monkeypatch):
        if not _owns_supabase(mod):
            pytest.skip(f"{mod.__name__} imports get_supabase from ai_helper")
        import supabase

        create = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(supabase, "create_client", create)
        with _mock_ssm(mod, SUPABASE_PARAMS):
            mod.get_supabase()
        create.assert_called_once_with("https://test.supabase.co", "test-service-key")

    def test_construction_failure_is_not_cached(self, mod, monkeypatch):
        """A failed create_client must leave the next call free to retry."""
        if not _owns_supabase(mod):
            pytest.skip(f"{mod.__name__} imports get_supabase from ai_helper")
        import supabase

        good = MagicMock(name="supabase_client")
        create = MagicMock(side_effect=[RuntimeError("dns"), good])
        monkeypatch.setattr(supabase, "create_client", create)
        with _mock_ssm(mod, SUPABASE_PARAMS):
            with pytest.raises(RuntimeError, match="dns"):
                mod.get_supabase()
            assert mod.get_supabase() is good
        assert create.call_count == 2


# ---------------------------------------------------------------------------
# reset_caches
# ---------------------------------------------------------------------------

class TestResetCaches:

    def test_reset_forces_a_fresh_param_read(self, mod):
        with _mock_ssm(mod, {"/rotating": "old"}) as ssm:
            assert mod.get_param("/rotating") == "old"
            ssm.get_parameter.side_effect = (
                lambda Name, WithDecryption=False: {"Parameter": {"Value": "new"}}
            )
            # Still cached — that is the point of the cache.
            assert mod.get_param("/rotating") == "old"
            mod.reset_caches()
            assert mod.get_param("/rotating") == "new"

    def test_reset_forces_a_fresh_client(self, mod, monkeypatch):
        if not _owns_supabase(mod):
            pytest.skip(f"{mod.__name__} imports get_supabase from ai_helper")
        import supabase

        first, second = MagicMock(name="c1"), MagicMock(name="c2")
        monkeypatch.setattr(supabase, "create_client", MagicMock(side_effect=[first, second]))
        with _mock_ssm(mod, SUPABASE_PARAMS):
            assert mod.get_supabase() is first
            mod.reset_caches()
            assert mod.get_supabase() is second

    def test_reset_leaves_the_ssm_client_alone(self, mod):
        """The boto3 handle is a connection holder, not a cached value.

        For lambdas/pipeline this also protects the lazy-init contract: _ssm
        must stay non-None once built, or _get_ssm() would rebuild a client
        (and re-resolve region/credentials) on the next call.
        """
        with _mock_ssm(mod, {"/x": "y"}) as ssm:
            mod.get_param("/x")
            mod.reset_caches()
            attr = "_ssm" if hasattr(mod, "_ssm") else "ssm"
            assert getattr(mod, attr) is ssm


# ---------------------------------------------------------------------------
# Coverage of the list itself
# ---------------------------------------------------------------------------

class TestCoverage:
    """MODULES must not drift from what is actually on disk."""

    def test_every_copy_is_covered(self):
        """Any module defining get_param must be in MODULES and must memoize.

        Guards the failure mode this whole change was about: a module quietly
        keeping an uncached copy while the rest of the codebase moved on.
        """
        root = pathlib.Path(__file__).resolve().parents[2]
        defines_get_param = set()
        for d in ("lambdas/pipeline", "lambdas/scrapers"):
            for path in sorted((root / d).glob("*.py")):
                if re.search(r"^def get_param\(", path.read_text(), re.M):
                    defines_get_param.add(path.stem)

        missing = defines_get_param - set(MODULES)
        assert not missing, (
            f"modules define get_param but are not covered by this test: "
            f"{sorted(missing)} — add them to MODULES and memoize their copy"
        )

    def test_every_listed_module_exposes_the_contract(self, mod):
        assert callable(mod.get_param)
        assert callable(mod.reset_caches)
        assert isinstance(mod._param_cache, dict)

    def test_no_listed_module_still_refetches_unconditionally(self, mod):
        """Source-level backstop: the pre-fix one-liner must be gone.

        The call-count tests above are the real guard; this catches a copy that
        was reverted or re-pasted without anyone noticing the counts changed.
        """
        src = inspect.getsource(mod.get_param)
        assert "_param_cache" in src, (
            f"{mod.__name__}.get_param does not consult a cache"
        )
        assert not re.match(
            r"^def get_param\(name\):\s*\n\s*return ", src
        ), f"{mod.__name__}.get_param still returns an unconditional fetch"
