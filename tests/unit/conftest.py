"""Unit test fixtures — everything mocked."""
import sys

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def reset_ai_helper_caches():
    """Clear ai_helper's memoized SSM params + Supabase client between tests.

    get_param() and get_supabase() cache at module scope for the life of the
    process, which is what we want in a Lambda container but not across tests:
    a value fetched through one test's boto3 mock would otherwise be served to
    every later test, whatever that test patched. Clear on the way in and out
    so each test starts from a cold cache.

    Both module identities get cleared. tests/conftest.py puts
    lambdas/pipeline on sys.path, so `ai_helper` (flat, used by most tests)
    and `lambdas.pipeline.ai_helper` (used by test_ai_helper_max_tokens.py)
    are two distinct module objects with independent caches.

    Only modules already in sys.modules are touched — importing them here
    instead would make every unit test load both spellings of ai_helper as a
    side effect of the fixture, which is exactly the kind of import-shape
    coupling tests/unit/test_deploy_path_parity.py exists to police.
    """
    def _clear():
        for name in ("ai_helper", "lambdas.pipeline.ai_helper"):
            mod = sys.modules.get(name)
            reset = getattr(mod, "reset_caches", None) if mod else None
            if reset is not None:
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
