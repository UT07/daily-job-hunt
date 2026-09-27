"""Coverage for SupabaseClient.upsert_search_config's graceful degradation
when an optional column doesn't exist in the live schema yet.

Concretely: supabase/migrations/20260506_user_search_configs_enabled_sources.sql
adds `enabled_sources` (backs the Settings -> "Save Sources" toggle) but has
never been applied to the live database (it goes through the dashboard, not
`supabase db push`). Before this fix, PUT /api/search-config's upsert raised
whenever the request included that column, and nothing caught it -- FastAPI's
default handling turned it into a 500 on every "Save Sources" click. This
retries without the offending column instead, so the endpoint degrades
gracefully both before AND after the migration is eventually applied.
"""
from unittest.mock import MagicMock, patch

import pytest

import db_client


def _make_client(execute_side_effect):
    """A SupabaseClient whose underlying `.table(...).upsert(...).execute()`
    chain is fully mocked, going through the real __init__ (with
    create_client patched out) rather than reaching into internals."""
    mock_supabase = MagicMock()
    chain = MagicMock()
    mock_supabase.table.return_value = chain
    chain.upsert.return_value = chain
    chain.execute.side_effect = execute_side_effect

    with patch("db_client.create_client", return_value=mock_supabase):
        client = db_client.SupabaseClient("https://example.supabase.co", "fake-service-key")
    return client, chain


def test_upsert_search_config_degrades_on_postgrest_schema_cache_wording():
    """PostgREST's own wording for an unrecognised column on write (the same
    shape already seen in this codebase for the match_jobs_semantic RPC in
    mcp_server.py, just for a column instead of a function)."""
    ok_result = MagicMock(data=[{"user_id": "u1", "queries": ["registered nurse"]}])
    err = Exception(
        "Could not find the 'enabled_sources' column of 'user_search_configs' "
        "in the schema cache"
    )
    client, chain = _make_client(execute_side_effect=[err, ok_result])

    result = client.upsert_search_config(
        "u1", {"queries": ["registered nurse"], "enabled_sources": ["linkedin"]}
    )

    assert result == {"user_id": "u1", "queries": ["registered nurse"]}
    assert chain.upsert.call_count == 2
    retried_payload = chain.upsert.call_args_list[1][0][0]
    assert "enabled_sources" not in retried_payload
    assert retried_payload["queries"] == ["registered nurse"]


def test_upsert_search_config_degrades_on_raw_postgres_wording():
    """Raw Postgres wording — the same shape score_batch.py's existing
    retry-without-optional-columns logic already matches for the `jobs`
    table (`"column" in str(e) and "does not exist" in str(e)`)."""
    ok_result = MagicMock(data=[{"user_id": "u2"}])
    err = Exception('column "enabled_sources" of relation "user_search_configs" does not exist')
    client, chain = _make_client(execute_side_effect=[err, ok_result])

    result = client.upsert_search_config("u2", {"enabled_sources": ["indeed"]})

    assert result == {"user_id": "u2"}
    retried_payload = chain.upsert.call_args_list[1][0][0]
    assert "enabled_sources" not in retried_payload


def test_upsert_search_config_reraises_unrelated_errors():
    """Only the narrow 'missing column' condition is swallowed. Anything
    else (network error, RLS violation, ...) must still propagate rather
    than being silently hidden from the caller."""
    client, chain = _make_client(execute_side_effect=[RuntimeError("connection reset")])

    with pytest.raises(RuntimeError, match="connection reset"):
        client.upsert_search_config("u3", {"queries": ["staff accountant"]})

    assert chain.upsert.call_count == 1


def test_upsert_search_config_succeeds_normally_once_column_exists():
    """Once the migration IS applied, the happy path is untouched: one
    upsert call, no retry."""
    ok_result = MagicMock(data=[{"user_id": "u4", "enabled_sources": ["linkedin"]}])
    client, chain = _make_client(execute_side_effect=[ok_result])

    result = client.upsert_search_config("u4", {"enabled_sources": ["linkedin"]})

    assert result == {"user_id": "u4", "enabled_sources": ["linkedin"]}
    assert chain.upsert.call_count == 1
