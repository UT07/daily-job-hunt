"""Unit tests for load_config Lambda."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_supabase_factory():
    """Return a configured mock Supabase client."""
    def _make(search_config_data=None, adjustments_data=None):
        mock_client = MagicMock()

        search_result = MagicMock()
        search_result.data = search_config_data if search_config_data is not None else []

        adj_result = MagicMock()
        adj_result.data = adjustments_data if adjustments_data is not None else []

        search_chain = MagicMock()
        search_chain.select.return_value = search_chain
        search_chain.eq.return_value = search_chain
        search_chain.execute.return_value = search_result

        adj_chain = MagicMock()
        adj_chain.select.return_value = adj_chain
        adj_chain.eq.return_value = adj_chain
        adj_chain.execute.return_value = adj_result

        def table_side_effect(name):
            if name == "user_search_configs":
                return search_chain
            elif name == "self_improvement_config":
                return adj_chain
            return MagicMock()

        mock_client.table.side_effect = table_side_effect
        return mock_client

    return _make


def test_returns_config_with_query_hash_and_user_id(mock_supabase_factory):
    """Config returned has a 12-char query_hash and user_id preserved."""
    db = mock_supabase_factory(
        search_config_data=[{
            "queries": ["python developer", "backend engineer"],
            "locations": ["dublin"],
            "sources": ["linkedin"],
            "min_match_score": 75,
        }],
        adjustments_data=[],
    )

    with patch("load_config.get_supabase", return_value=db):
        import load_config
        result = load_config.handler({"user_id": "user-42"}, None)

    assert result["user_id"] == "user-42"
    assert "query_hash" in result
    assert len(result["query_hash"]) == 12
    assert result["min_match_score"] == 75


def test_uses_default_config_when_no_search_config_found(mock_supabase_factory):
    """When user_search_configs returns no rows, default config is used."""
    db = mock_supabase_factory(
        search_config_data=[],   # no rows
        adjustments_data=[],
    )

    with patch("load_config.get_supabase", return_value=db):
        import load_config
        result = load_config.handler({"user_id": "user-99"}, None)

    assert result["user_id"] == "user-99"
    # Default min_match_score is 60
    assert result["min_match_score"] == 60
    assert "query_hash" in result
    assert len(result["query_hash"]) == 12
    # Default queries should be present
    assert "software engineer" in result["queries"]
