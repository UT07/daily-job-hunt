"""Unit tests for mcp_server.server.

These intentionally diverge from the original plan's test fixtures (docs/
superpowers/plans/2026-09-22-ey-genai-platform-upgrade.md Task 26 Step 2),
which asserted against `final_score` / `match_reasoning`. Neither key is
real: `score_single_job`'s actual return shape (see
lambdas/pipeline/score_batch.py SCORE_SYSTEM_PROMPT and the JSON-parsing
branch right after it) uses `match_score` and `reasoning`. `final_score` and
`score_status` on the `jobs` table are dead columns nothing reliably writes
or reads.
"""
from unittest.mock import MagicMock, patch

import pytest

from mcp_server import server


# ---------------------------------------------------------------------------
# score_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_job_returns_tier_and_score_from_match_score():
    with patch.object(
        server,
        "score_single_job",
        return_value={"match_score": 88.0, "ats_score": 91.0, "reasoning": "strong"},
    ):
        out = await server.score_job("Backend engineer, Python, AWS.", resume_tex="dummy resume text")
    assert out["score"] == 88.0
    assert out["tier"] == "A"  # score_to_tier: S=90+, A=80-89, B=70-79, C=60-69, D<60
    assert out["ats_score"] == 91.0
    assert out["reasoning"] == "strong"


@pytest.mark.asyncio
async def test_score_job_defaults_missing_score_to_d_tier():
    with patch.object(server, "score_single_job", return_value=None):
        out = await server.score_job("garbled JD", resume_tex="dummy resume text")
    assert out["score"] == 0.0
    assert out["tier"] == "D"


@pytest.mark.asyncio
async def test_score_job_loads_base_resume_when_not_supplied():
    """resume_tex="" would silently score against nothing -- score_job must
    never do that. When the caller omits resume_tex, it loads the user's
    real base resume instead of defaulting to an empty string.
    """
    with patch.object(server, "_load_base_resume", return_value="a real base resume") as load, patch.object(
        server, "score_single_job", return_value={"match_score": 70.0}
    ) as scorer:
        await server.score_job("Some JD")

    load.assert_called_once_with(server.DEFAULT_USER_ID)
    assert scorer.call_args.kwargs["resume_tex"] == "a real base resume"
    assert scorer.call_args.kwargs["resume_tex"] != ""


def test_load_base_resume_raises_rather_than_returning_empty_string():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"tex_content": ""}
    ]
    with patch.object(server, "_db", return_value=db):
        with pytest.raises(ValueError, match="no base resume"):
            server._load_base_resume("some-user-id")


# ---------------------------------------------------------------------------
# search_jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_jobs_returns_ranked_titles():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        {"job_hash": "a", "title": "Platform Engineer", "similarity": 0.91},
    ]
    with patch.object(server, "embed", return_value=[0.1] * 768), patch.object(server, "_db", return_value=db):
        out = await server.search_jobs("platform work", limit=5)
    assert out[0]["title"] == "Platform Engineer"
    assert out[0]["match_mode"] == "semantic"


@pytest.mark.asyncio
async def test_search_jobs_falls_back_to_keyword_search_when_rpc_missing():
    """The match_jobs_semantic migration this task adds is written but not
    applied yet (db push is blocked; see the migration file). search_jobs
    must still return real results against the live `jobs` table today.
    """
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = Exception(
        "Could not find the function public.match_jobs_semantic(p_embedding, p_k) in the schema cache"
    )
    title_hit = {"job_hash": "b", "title": "Platform Engineer", "company": "Acme", "match_score": 91, "score_tier": "S"}
    title_chain = db.table.return_value.select.return_value.eq.return_value.ilike.return_value.order.return_value.limit.return_value
    title_chain.execute.return_value.data = [title_hit]

    with patch.object(server, "embed", return_value=[0.1] * 768), patch.object(server, "_db", return_value=db):
        out = await server.search_jobs("platform work", limit=5)

    assert out[0]["title"] == "Platform Engineer"
    assert out[0]["match_mode"] == "keyword"
    assert out[0]["similarity"] is None


@pytest.mark.asyncio
async def test_search_jobs_rejects_an_oversized_limit():
    # An unbounded limit lets one MCP call pull far more than a page of the
    # PostgREST-backed `jobs` table.
    with pytest.raises(ValueError, match="limit"):
        await server.search_jobs("anything", limit=5000)


@pytest.mark.asyncio
async def test_search_jobs_rejects_a_zero_or_negative_limit():
    with pytest.raises(ValueError, match="limit"):
        await server.search_jobs("anything", limit=0)


def test_max_limit_stays_well_under_the_postgrest_page_cap():
    # PostgREST silently truncates any unbounded response at 1000 rows --
    # this has bitten the project four times (CLAUDE.md backlog). MAX_LIMIT
    # must never creep anywhere near that ceiling.
    assert server.MAX_LIMIT < 1000


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_returns_none_for_unknown_id():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with patch.object(server, "_db", return_value=db):
        assert await server.get_job("nope") is None


@pytest.mark.asyncio
async def test_get_job_returns_live_score_columns_not_dead_ones():
    db = MagicMock()
    row = {
        "job_hash": "abc",
        "title": "SRE",
        "company": "Acme",
        "location": "Dublin",
        "match_score": 91,
        "ats_score": 93,
        "score_tier": "S",
        "application_status": "New",
    }
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [row]
    with patch.object(server, "_db", return_value=db):
        out = await server.get_job("abc")

    assert out == row
    selected_columns = db.table.return_value.select.call_args[0][0]
    assert "match_score" in selected_columns
    assert "ats_score" in selected_columns
    assert "score_tier" in selected_columns
    assert "final_score" not in selected_columns
    assert "score_status" not in selected_columns


# ---------------------------------------------------------------------------
# server wiring
# ---------------------------------------------------------------------------


def test_build_server_registers_all_three_tools():
    names = {t.name for t in server.build_server()._tool_manager.list_tools()}
    assert {"search_jobs", "score_job", "get_job"} <= names
