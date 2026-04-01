"""Unit test fixtures — everything mocked."""
import pytest
from unittest.mock import patch, MagicMock


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
