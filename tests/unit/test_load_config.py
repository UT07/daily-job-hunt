"""Unit tests for load_config Lambda."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_supabase_factory():
    """Return a configured mock Supabase client."""
    def _make(search_config_data=None, adjustments_data=None, pipeline_adjustments_data=None):
        mock_client = MagicMock()

        search_result = MagicMock()
        search_result.data = search_config_data if search_config_data is not None else []

        adj_result = MagicMock()
        adj_result.data = adjustments_data if adjustments_data is not None else []

        pipeline_adj_result = MagicMock()
        pipeline_adj_result.data = pipeline_adjustments_data if pipeline_adjustments_data is not None else []

        search_chain = MagicMock()
        search_chain.select.return_value = search_chain
        search_chain.eq.return_value = search_chain
        search_chain.execute.return_value = search_result

        adj_chain = MagicMock()
        adj_chain.select.return_value = adj_chain
        adj_chain.eq.return_value = adj_chain
        adj_chain.execute.return_value = adj_result

        pipeline_adj_chain = MagicMock()
        pipeline_adj_chain.select.return_value = pipeline_adj_chain
        pipeline_adj_chain.in_.return_value = pipeline_adj_chain
        pipeline_adj_chain.eq.return_value = pipeline_adj_chain
        pipeline_adj_chain.execute.return_value = pipeline_adj_result

        def table_side_effect(name):
            if name == "user_search_configs":
                return search_chain
            elif name == "self_improvement_config":
                return adj_chain
            elif name == "pipeline_adjustments":
                return pipeline_adj_chain
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


# --- Tests for load_config_with_adjustments ---


def test_load_config_merges_adjustments():
    """Active adjustments override base config values."""
    mock_db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    result = MagicMock()
    result.data = [
        {"id": "adj-1", "adjustment_type": "score_threshold",
         "payload": {"min_match_score": 40}, "status": "auto_applied"},
    ]
    chain.execute.return_value = result
    mock_db.table.return_value = chain

    with patch("load_config.get_supabase", return_value=mock_db):
        from load_config import load_config_with_adjustments
        config = load_config_with_adjustments({"min_match_score": 50}, "test-user")

    assert config["min_match_score"] == 40
    assert config["_active_adjustments"] == ["adj-1"]


def test_approved_overrides_auto_applied():
    """Approved adjustment wins over auto_applied for same key."""
    mock_db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    result = MagicMock()
    result.data = [
        {"id": "adj-1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
        {"id": "adj-2", "payload": {"min_match_score": 35}, "status": "approved"},
    ]
    chain.execute.return_value = result
    mock_db.table.return_value = chain

    with patch("load_config.get_supabase", return_value=mock_db):
        from load_config import load_config_with_adjustments
        config = load_config_with_adjustments({"min_match_score": 50}, "test-user")

    assert config["min_match_score"] == 35  # approved wins
    assert set(config["_active_adjustments"]) == {"adj-1", "adj-2"}


def test_no_adjustments_preserves_base():
    """When no pipeline adjustments exist, base config is preserved."""
    mock_db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    result = MagicMock()
    result.data = []
    chain.execute.return_value = result
    mock_db.table.return_value = chain

    with patch("load_config.get_supabase", return_value=mock_db):
        from load_config import load_config_with_adjustments
        config = load_config_with_adjustments({"min_match_score": 50}, "test-user")

    assert config["min_match_score"] == 50
    assert config["_active_adjustments"] == []


def test_adjustments_do_not_mutate_base_config():
    """The original base_config dict must not be modified."""
    mock_db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.in_.return_value = chain
    chain.eq.return_value = chain
    result = MagicMock()
    result.data = [
        {"id": "adj-1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
    ]
    chain.execute.return_value = result
    mock_db.table.return_value = chain

    base = {"min_match_score": 50}
    with patch("load_config.get_supabase", return_value=mock_db):
        from load_config import load_config_with_adjustments
        load_config_with_adjustments(base, "test-user")

    assert base["min_match_score"] == 50  # original unchanged
    assert "_active_adjustments" not in base


def test_handler_merges_pipeline_adjustments(mock_supabase_factory):
    """Handler integrates pipeline_adjustments into final config."""
    db = mock_supabase_factory(
        search_config_data=[{
            "queries": ["software engineer"],
            "locations": ["ireland"],
            "sources": ["linkedin"],
            "min_match_score": 60,
        }],
        adjustments_data=[],
        pipeline_adjustments_data=[
            {"id": "adj-1", "payload": {"min_match_score": 45}, "status": "approved"},
        ],
    )

    with patch("load_config.get_supabase", return_value=db):
        import load_config
        result = load_config.handler({"user_id": "user-100"}, None)

    assert result["min_match_score"] == 45
    assert result["_active_adjustments"] == ["adj-1"]
