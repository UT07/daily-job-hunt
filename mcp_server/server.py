"""MCP server exposing the NaukriBaba pipeline as tools.

Thin by design: every tool delegates to code already serving the REST API
(`score_single_job`, `retrieval.embeddings.embed`, the same Supabase `jobs`
/ `user_resumes` tables `/api/score` and `/api/dashboard/jobs` read), so
there is one implementation of each behaviour and MCP is a second
transport, not a second system.

Ground truth this module encodes, corrected from the original plan
(docs/superpowers/plans/2026-09-22-ey-genai-platform-upgrade.md Task 26):

- `final_score` and `score_status` on `jobs` are dead columns — nothing
  reliably writes or reads them (score_batch.py's insert never sets
  `score_status`, and leaves `final_score` null for every job scored since).
  The live signal is `match_score`, `ats_score`, and `score_tier`.
- `score_single_job`'s real return dict (see lambdas/pipeline/score_batch.py
  SCORE_SYSTEM_PROMPT) uses the keys `match_score` and `reasoning` — not
  `final_score` / `match_reasoning`. Scoring must never run against an
  empty resume string; `resume_tex` here is optional and, when omitted,
  loads the caller's real base resume from `user_resumes`.
- `retrieval` lives at `lambdas/pipeline/retrieval/`, not at a repo-root
  `retrieval` package.
- PostgREST caps any unbounded response at 1000 rows (this project has hit
  that four times); every list-shaped tool takes a small, validated
  `limit` via MAX_LIMIT well under that ceiling.

Import style: this package lives at the repo root, exactly like `app.py`,
and — like `app.py` — is reached only from the container-image Lambda
(Dockerfile.lambda COPYs the whole `lambdas/` tree there) or from a local
`python -m mcp_server.server` run; never from a zip-based pipeline Lambda
whose CodeUri flattens `lambdas/pipeline/` into a bare `/var/task`. So,
exactly like `app.py`'s own `from lambdas.pipeline.parse_sections import
...`, the qualified `lambdas.pipeline...` spelling is used directly and
unguarded below — there is no flattened-CodeUri shape for this module to
also support, unlike modules that live *inside* lambdas/pipeline/ (see
retrieval/embeddings.py's docstring for that other case).

One real wrinkle found by actually running this (not just importing it):
`lambdas/pipeline/score_batch.py` itself imports its sibling unqualified
(`from ai_helper import ai_complete_cached, get_supabase`), which only
resolves when `ai_helper` is reachable under that flat name — true in
pytest (tests/conftest.py puts `lambdas/pipeline` on sys.path) and true in
the deployed zip Lambda (CodeUri flattens `lambdas/pipeline/` into
`/var/task`, where `ai_helper` is a flat sibling module) — but NOT true
for a plain `python -m mcp_server.server` process, whose sys.path has the
repo root, not `lambdas/pipeline/`. Confirmed live: without the shim below,
importing score_batch raises `ModuleNotFoundError: No module named
'ai_helper'` from score_batch.py's own top-level import line, and the
stdio server never starts.

score_batch.py is out of scope to change here. The fix is deliberately NOT
`sys.path.insert(0, ".../lambdas/pipeline")`: `lambdas/pipeline/utils/`
mirrors the repo-root `utils/` package filename-for-filename (both have
canonical_hash.py, keyword_extractor.py, ...), and app.py itself does
`from utils.canonical_hash import canonical_hash` — inserting
lambdas/pipeline onto sys.path ahead of the repo root would silently
shadow that import with the wrong module the moment anything imports
mcp_server in the same process as app.py. Registering the already-and-
correctly-dotted-imported module under its flat name in sys.modules
achieves the same resolution for score_batch.py's own import line without
touching sys.path at all.
"""
from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from lambdas.pipeline import ai_helper as _ai_helper

sys.modules.setdefault("ai_helper", _ai_helper)  # see wrinkle above

from lambdas.pipeline.retrieval.embeddings import embed
from lambdas.pipeline.score_batch import score_single_job, score_to_tier

get_supabase = _ai_helper.get_supabase

logger = logging.getLogger(__name__)

# PostgREST silently caps any unbounded `.select()`/RPC response at 1000
# rows — see CLAUDE.md backlog and store.py callers for prior incidents.
# Every list-shaped tool below validates against this instead of ever
# issuing an unbounded query; MAX_LIMIT itself stays far below that ceiling.
MAX_LIMIT = 50

# Single-tenant today (CLAUDE.md "Scaling Vision": get it working for one
# user first). Neither MCP transport carries a per-tool-call identity: stdio
# has no auth at all, and the SSE mount's Supabase JWT (mcp_server.http_auth)
# authenticates the HTTP connection, not an individual tool argument. Every
# tool below is therefore pinned server-side to this one known account
# rather than trusting a client-supplied user_id, which would otherwise let
# any connected MCP client read (or score against) an arbitrary tenant's
# private job-hunt data.
DEFAULT_USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"

_JOB_COLUMNS = "job_hash, title, company, location, match_score, ats_score, score_tier, application_status"


def _db():
    return get_supabase()


def _load_base_resume(user_id: str) -> str:
    """Latest resume `tex_content` for a user.

    Mirrors score_batch.py's own handler exactly: no `is_active` column, so
    "latest" means most-recently-created. Raises rather than falling back to
    "" — scoring against no resume at all is not a conservative score, it is
    a meaningless one.
    """
    rows = (
        _db()
        .table("user_resumes")
        .select("tex_content")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    tex_content = (rows[0].get("tex_content") if rows else None) or ""
    if not tex_content:
        raise ValueError(f"no base resume on file for user {user_id}")
    return tex_content


async def search_jobs(query: str, limit: int = 10) -> list[dict]:
    """Search this user's scraped jobs by meaning, not just keywords.

    Ranks by pgvector cosine similarity against `jobs.embedding` via the
    `match_jobs_semantic` RPC (supabase/migrations/
    20260926000000_match_jobs_semantic.sql) when that RPC is reachable.
    Falls back to a plain title/description keyword search against the same
    live `jobs` table when it is not — e.g. before that migration has been
    applied through the Supabase dashboard (this repo's `supabase db push`
    is currently blocked; see the migration file's header). Every result
    carries `match_mode` ("semantic" or "keyword") so a caller can tell
    which path actually ran.
    """
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    db = _db()
    try:
        rows = (
            db.rpc(
                "match_jobs_semantic",
                {"p_user_id": DEFAULT_USER_ID, "p_embedding": embed(query), "p_k": limit},
            )
            .execute()
            .data
            or []
        )
        return [
            {
                "job_hash": r["job_hash"],
                "title": r["title"],
                "company": r.get("company", ""),
                "match_score": r.get("match_score"),
                "score_tier": r.get("score_tier"),
                "similarity": round(r.get("similarity", 0) or 0, 3),
                "match_mode": "semantic",
            }
            for r in rows[:limit]
        ]
    except Exception as exc:
        logger.warning("[mcp] match_jobs_semantic unavailable (%s) — falling back to keyword search", exc)
        return _keyword_search_jobs(db, query, limit)


def _keyword_search_jobs(db, query: str, limit: int) -> list[dict]:
    """Title/description substring search over the live `jobs` table.

    Two separate `ilike` queries (rather than one `.or_()` filter) so a
    query string containing a comma can't be misparsed as multiple
    PostgREST filter clauses.
    """
    cols = "job_hash, title, company, match_score, score_tier"
    by_title = (
        db.table("jobs")
        .select(cols)
        .eq("user_id", DEFAULT_USER_ID)
        .ilike("title", f"%{query}%")
        .order("match_score", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    seen = {r["job_hash"] for r in by_title}
    remaining = limit - len(by_title)
    by_description = []
    if remaining > 0:
        by_description = (
            db.table("jobs")
            .select(cols)
            .eq("user_id", DEFAULT_USER_ID)
            .ilike("description", f"%{query}%")
            .order("match_score", desc=True)
            .limit(remaining + len(seen))
            .execute()
            .data
            or []
        )
        by_description = [r for r in by_description if r["job_hash"] not in seen][:remaining]
    return [{**r, "similarity": None, "match_mode": "keyword"} for r in (by_title + by_description)[:limit]]


async def score_job(jd_text: str, resume_tex: str | None = None) -> dict:
    """Score a job description against the candidate's base resume.

    `resume_tex` is optional — when omitted, the latest resume on file for
    the single known user (DEFAULT_USER_ID) is loaded from `user_resumes`,
    the same source `/api/score` reads. It is never defaulted to an empty
    string.
    """
    if resume_tex is None:
        resume_tex = _load_base_resume(DEFAULT_USER_ID)

    result = (
        score_single_job(
            {"title": "", "company": "", "description": jd_text},
            resume_tex=resume_tex,
            temperature=0,
        )
        or {}
    )
    match_score = result.get("match_score", 0.0)
    return {
        "score": match_score,
        "tier": score_to_tier(match_score),
        "ats_score": result.get("ats_score"),
        "reasoning": result.get("reasoning", ""),
    }


async def get_job(job_hash: str) -> dict | None:
    """Fetch one stored job by hash.

    Selects only live columns — `final_score` and `score_status` are dead
    (see module docstring) and are deliberately never surfaced here.
    """
    rows = (
        _db()
        .table("jobs")
        .select(_JOB_COLUMNS)
        .eq("job_hash", job_hash)
        .eq("user_id", DEFAULT_USER_ID)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def build_server() -> FastMCP:
    mcp = FastMCP("naukribaba")
    mcp.tool()(search_jobs)
    mcp.tool()(score_job)
    mcp.tool()(get_job)
    return mcp


if __name__ == "__main__":
    build_server().run()
