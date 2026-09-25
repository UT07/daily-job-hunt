"""Unit test fixtures — everything mocked."""
import sys

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def reset_ssm_param_caches():
    """Clear every Lambda module's memoized SSM params + Supabase client.

    get_param() and get_supabase() cache at module scope for the life of the
    process, which is what we want in a Lambda container but not across tests:
    a value fetched through one test's boto3 mock would otherwise be served to
    every later test, whatever that test patched. Clear on the way in and out
    so each test starts from a cold cache.

    Discovered by signature (a module exporting both reset_caches and
    _param_cache) rather than from a hardcoded list, because there are 21 such
    modules across lambdas/pipeline/ and lambdas/scrapers/ — each carrying its
    own copy, since the two directories are separate CodeUris and cannot share
    one helper. A list would silently miss the next copy added; this does not.

    Walks sys.modules rather than importing anything. Importing here would make
    every unit test load both the flat (`ai_helper`) and qualified
    (`lambdas.pipeline.ai_helper`) spellings — two distinct module objects with
    independent caches — as a side effect of the fixture, which is exactly the
    import-shape coupling tests/unit/test_deploy_path_parity.py exists to
    police. Anything not yet imported has no cache to clear anyway.
    """
    def _clear():
        # Snapshot: reset_caches() imports nothing, but sys.modules must not be
        # mutated mid-walk by anything else either.
        for mod in list(sys.modules.values()):
            reset = getattr(mod, "reset_caches", None)
            if reset is not None and hasattr(mod, "_param_cache"):
                reset()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def patch_boto3_ssm():
    """Prevent any real AWS calls in unit tests."""
    with patch("boto3.client") as mock_client:
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "mock-value"}
        }
        mock_client.return_value = mock_ssm
        yield mock_client
