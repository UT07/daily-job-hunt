"""`search_jobs` must not pay for an embedding it is going to throw away.

Measured live against the production database on 2026-09-30:

    POST /rest/v1/rpc/match_jobs_semantic -> 404 PGRST202
      "Searched for the function public.match_jobs_semantic with parameters
       p_embedding, p_k, p_user_id ... but no matches were found in the
       schema cache"  (hint: "Perhaps you meant ... match_jobs_in_company")

So every `search_jobs` call fell through to the `ilike` keyword branch — but
only after `embed(query)` had already made a Gemini `embedContent` round trip
whose 768 floats were then discarded. 1,232 of this user's 1,280 job rows do
carry an embedding, so the semantic path is worth having; it is the ordering
that was wrong, not the feature.

The RPC's availability is therefore probed with a zero vector and `p_k=0`
before anything is embedded, and the verdict is cached for the process. A
`LIMIT 0` query does no vector work, so the probe costs one round trip to a
database the tool is about to query anyway — against a Gemini call on every
single search.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lambdas.pipeline.retrieval.embeddings import EMBED_DIM
from mcp_server import identity, server


TEST_CALLER = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _authenticated_caller():
    """Tools refuse to run without a caller (mcp_server.identity); act as one.
    test_mcp_caller_identity.py owns the cross-user and no-caller cases."""
    with identity.acting_as(TEST_CALLER):
        yield

MISSING_FUNCTION = (
    "Could not find the function public.match_jobs_semantic(p_embedding, p_k, "
    "p_user_id) in the schema cache"
)


@pytest.fixture(autouse=True)
def _forget_the_rpc_probe():
    server.reset_semantic_probe()
    yield
    server.reset_semantic_probe()


def _keyword_db(exc: Exception):
    """A db double whose rpc always raises `exc` and whose ilike search hits."""
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = exc
    hit = {"job_hash": "b", "title": "Platform Engineer", "company": "Acme",
           "match_score": 91, "score_tier": "S"}
    chain = db.table.return_value.select.return_value.eq.return_value.ilike.return_value.order.return_value.limit.return_value
    chain.execute.return_value.data = [hit]
    return db


@pytest.mark.asyncio
async def test_no_embedding_is_bought_when_the_rpc_is_absent():
    """The defect, stated as a test: this fails if embed() is called at all."""
    db = _keyword_db(Exception(MISSING_FUNCTION))
    with patch.object(server, "embed") as embed, patch.object(server, "_db", return_value=db):
        out = await server.search_jobs("platform work", limit=5)

    embed.assert_not_called()
    assert out[0]["match_mode"] == "keyword"


@pytest.mark.asyncio
async def test_the_probe_never_embeds_and_carries_the_real_vector_width():
    """A probe vector of the wrong width would 404 for the wrong reason.

    Sized from retrieval.embeddings.EMBED_DIM rather than a literal 768, so
    a Matryoshka dimension change cannot silently turn the probe into a
    permanent false negative.
    """
    db = _keyword_db(Exception(MISSING_FUNCTION))
    with patch.object(server, "embed"), patch.object(server, "_db", return_value=db):
        await server.search_jobs("platform work", limit=5)

    probe_args = db.rpc.call_args_list[0].args
    assert probe_args[0] == "match_jobs_semantic"
    assert len(probe_args[1]["p_embedding"]) == EMBED_DIM
    assert set(probe_args[1]["p_embedding"]) == {0.0}
    assert probe_args[1]["p_k"] == 0


@pytest.mark.asyncio
async def test_a_second_search_does_not_re_probe_a_known_missing_rpc():
    db = _keyword_db(Exception(MISSING_FUNCTION))
    with patch.object(server, "embed") as embed, patch.object(server, "_db", return_value=db):
        await server.search_jobs("one", limit=5)
        await server.search_jobs("two", limit=5)

    assert db.rpc.call_count == 1, "probed twice for a verdict that cannot change"
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_a_transient_rpc_error_is_not_cached_as_a_verdict():
    """CLAUDE.md rule 3 in miniature: do not read one failure as a fact.

    A timeout is not "the migration was never applied". Caching it would
    demote every later search in the process to keyword mode for the wrong
    reason, and the `match_mode` field would say "keyword" with no way to
    tell why.
    """
    db = _keyword_db(Exception("timed out reading from the connection"))
    with patch.object(server, "embed"), patch.object(server, "_db", return_value=db):
        await server.search_jobs("one", limit=5)

    assert server.semantic_probe_verdict() is None


@pytest.mark.asyncio
async def test_the_embedding_is_bought_once_the_rpc_answers():
    db = MagicMock()
    # probe returns [] (LIMIT 0), the real query returns a ranked row
    db.rpc.return_value.execute.side_effect = [
        MagicMock(data=[]),
        MagicMock(data=[{"job_hash": "a", "title": "Platform Engineer", "similarity": 0.91}]),
    ]
    with patch.object(server, "embed", return_value=[0.25] * EMBED_DIM) as embed, patch.object(
        server, "_db", return_value=db
    ):
        out = await server.search_jobs("platform work", limit=5)

    embed.assert_called_once_with("platform work")
    assert out[0]["match_mode"] == "semantic"
    assert out[0]["similarity"] == 0.91
    # the second rpc call is the real one and carries the embedding
    assert db.rpc.call_args_list[1].args[1]["p_embedding"] == [0.25] * EMBED_DIM
    assert db.rpc.call_args_list[1].args[1]["p_k"] == 5


@pytest.mark.asyncio
async def test_a_later_semantic_search_skips_the_probe():
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = [
        MagicMock(data=[]),
        MagicMock(data=[{"job_hash": "a", "title": "One", "similarity": 0.9}]),
        MagicMock(data=[{"job_hash": "b", "title": "Two", "similarity": 0.8}]),
    ]
    with patch.object(server, "embed", return_value=[0.25] * EMBED_DIM), patch.object(
        server, "_db", return_value=db
    ):
        await server.search_jobs("one", limit=5)
        second = await server.search_jobs("two", limit=5)

    assert db.rpc.call_count == 3  # probe + two real queries
    assert second[0]["title"] == "Two"


@pytest.mark.asyncio
async def test_the_rpc_vanishing_after_a_positive_probe_still_falls_back():
    """A schema reload between the probe and the query must not 500 the tool."""
    db = MagicMock()
    hit = {"job_hash": "b", "title": "Platform Engineer", "company": "Acme",
           "match_score": 91, "score_tier": "S"}
    chain = db.table.return_value.select.return_value.eq.return_value.ilike.return_value.order.return_value.limit.return_value
    chain.execute.return_value.data = [hit]
    db.rpc.return_value.execute.side_effect = [MagicMock(data=[]), Exception(MISSING_FUNCTION)]

    with patch.object(server, "embed", return_value=[0.25] * EMBED_DIM), patch.object(
        server, "_db", return_value=db
    ):
        out = await server.search_jobs("platform work", limit=5)

    assert out[0]["match_mode"] == "keyword"
    assert server.semantic_probe_verdict() is False


@pytest.mark.asyncio
async def test_the_migration_file_the_operator_has_to_run_still_exists():
    """The other half of the fix is a file, not code.

    Removing the round trip makes the keyword fallback free; it does not make
    search semantic. That needs supabase/migrations/
    20260926000000_match_jobs_semantic.sql applied through the SQL editor
    (`supabase db push` is blocked by three duplicate 20260430 version
    prefixes). If this file ever disappears, the probe caches False forever
    and `search_jobs` is a keyword search with a semantic docstring.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    sql = repo / "supabase" / "migrations" / "20260926000000_match_jobs_semantic.sql"
    assert sql.is_file()
    body = sql.read_text()
    assert "create or replace function public.match_jobs_semantic" in body
    assert "p_user_id uuid" in body, "an unscoped RPC would leak other tenants' rows"
