"""MCP server exposing the NaukriBaba pipeline as tools.

Thin by design: every tool delegates to code already serving the REST API
(`score_single_job_deterministic`, `retrieval.embeddings.embed`, the same
Supabase `jobs` / `user_resumes` tables `/api/score` and
`/api/dashboard/jobs` read), so there is one implementation of each
behaviour and MCP is a second transport, not a second system.

"Thin" is a claim that has to be checked, and on 2026-09-30 it was false in
three places, all fixed here and all covered by tests/unit/
test_mcp_score_parity.py, test_mcp_search_jobs.py and
test_mcp_transport_reachable.py:

- `score_job` never applied `apply_geo_score_cap` and passed no `location`,
  so it could return S-tier for a job the pipeline records as B, and it
  returned a single bare number where the REST path returns the four scores
  and a `score_spread` band.
- `search_jobs` bought a Gemini embedding on every call and threw it away
  whenever `match_jobs_semantic` turned out to be missing — which, verified
  against production, was every call. Availability is now settled first,
  with a probe that costs no model call.
- nothing on the scoring path imported `guardrails/`, so client-supplied
  `jd_text` and `resume_tex` went straight into a prompt. They now go
  through `score_single_job(..., untrusted_input=True)`.

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
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from lambdas.pipeline import ai_helper as _ai_helper

sys.modules.setdefault("ai_helper", _ai_helper)  # see wrinkle above

from lambdas.pipeline.retrieval.embeddings import EMBED_DIM, embed
from lambdas.pipeline.score_batch import score_single_job_deterministic, score_to_tier
from shared.work_auth import apply_geo_score_cap

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

# Three uncached calls per score, matching app.py's `_score_rebuilt_resume`.
# Not a knob: the spread this produces is the whole reason `score_job` can
# report a band instead of a single integer, and skip_cache must be True for
# num_calls > 1 to mean anything (score_single_job_deterministic's docstring).
# The batch pipeline deliberately keeps one cached call — it scores ~58 jobs a
# run against an 8k tokens/minute ceiling — but an MCP tool call is one job
# asked for by a human, so it gets the honest measurement.
SCORE_CALLS = 3

SEMANTIC_RPC = "match_jobs_semantic"

# Hosts the MCP HTTP transport will answer for. The SDK's DNS-rebinding
# protection (mcp/server/transport_security.py) is what returned 421 "Invalid
# Host header" to every authenticated request against the deployed endpoint:
# FastMCP auto-enables it whenever `host` is a loopback address — which the
# default is — with only these three localhost patterns allowed. The deployed
# host has to be named, and it comes from the environment so the allowed set is
# whatever the stack actually serves (template.yaml passes MCP_ALLOWED_HOSTS
# from the HttpApi id). Protection stays ON: switching it off would also stop
# the 421, and that is not the same thing as fixing it.
LOCAL_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


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


# Verdict on whether SEMANTIC_RPC exists, for this process. None = not asked
# yet. Cached because the answer only changes when someone applies a migration,
# and re-asking costs a round trip per search.
_semantic_rpc_available: bool | None = None


def reset_semantic_probe() -> None:
    """Forget the cached RPC verdict. For tests, and for a long-lived process
    that wants to pick up a migration without a restart."""
    global _semantic_rpc_available
    _semantic_rpc_available = None


def semantic_probe_verdict() -> bool | None:
    """The cached verdict, or None if the RPC has not been probed yet."""
    return _semantic_rpc_available


def _is_missing_function(exc: Exception) -> bool:
    """True when PostgREST said the function does not exist.

    PGRST202 is the missing-FUNCTION code; PGRST204 is the missing-COLUMN one
    (see MEMORY postgrest_error_wording). Both mention "schema cache", so
    matching on that phrase alone would also swallow a column error. Live
    wording, 2026-09-30: "Could not find the function
    public.match_jobs_semantic(p_embedding, p_k, p_user_id) in the schema
    cache".

    Anything else — a timeout, a 5xx, a dropped connection — is NOT an answer
    to "was the migration applied", so it must not be cached as one.
    """
    text = str(exc).lower()
    return "pgrst202" in text or "could not find the function" in text


def _semantic_search_available(db) -> bool:
    """Is the semantic RPC there? Asked without paying for an embedding.

    A zero vector and `p_k=0` reach the same PostgREST function-resolution
    step a real call would, and `LIMIT 0` means Postgres does no vector work.
    That is the point of the ordering: `embed()` is a Gemini `embedContent`
    round trip, and before this the tool made one on every search and then
    threw the result away whenever this RPC turned out to be missing — which,
    verified against production on 2026-09-30, was every single time.
    """
    global _semantic_rpc_available
    if _semantic_rpc_available is not None:
        return _semantic_rpc_available

    try:
        db.rpc(
            SEMANTIC_RPC,
            {"p_user_id": DEFAULT_USER_ID, "p_embedding": [0.0] * EMBED_DIM, "p_k": 0},
        ).execute()
    except Exception as exc:
        if _is_missing_function(exc):
            logger.warning(
                "[mcp] %s is not in the database — keyword search only until "
                "supabase/migrations/20260926000000_match_jobs_semantic.sql is applied (%s)",
                SEMANTIC_RPC, exc,
            )
            _semantic_rpc_available = False
        else:
            logger.warning(
                "[mcp] could not probe %s (%s) — keyword search for this call, "
                "will retry on the next one",
                SEMANTIC_RPC, exc,
            )
        return False

    _semantic_rpc_available = True
    return True


async def search_jobs(query: str, limit: int = 10) -> list[dict]:
    """Search this user's scraped jobs by meaning, not just keywords.

    Ranks by pgvector cosine similarity against `jobs.embedding` via the
    `match_jobs_semantic` RPC (supabase/migrations/
    20260926000000_match_jobs_semantic.sql) when that RPC is reachable, and
    falls back to a title/description keyword search over the same live
    `jobs` table when it is not — e.g. before that migration has been applied
    through the Supabase dashboard (this repo's `supabase db push` is
    currently blocked; see the migration file's header).

    Whether the RPC exists is settled first, with a probe that costs one
    database round trip and no Gemini call, so the keyword path never pays for
    an embedding it discards. Every result carries `match_mode` ("semantic" or
    "keyword") so a caller can tell which path actually ran.
    """
    global _semantic_rpc_available

    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    db = _db()
    if not _semantic_search_available(db):
        return _keyword_search_jobs(db, query, limit)

    try:
        rows = (
            db.rpc(
                SEMANTIC_RPC,
                {"p_user_id": DEFAULT_USER_ID, "p_embedding": embed(query), "p_k": limit},
            )
            .execute()
            .data
            or []
        )
    except Exception as exc:
        # The probe said yes and the query said no: a schema reload between the
        # two, or the RPC dropped. Re-record the verdict so the next search
        # skips the wasted embedding too.
        if _is_missing_function(exc):
            _semantic_rpc_available = False
        logger.warning("[mcp] %s failed (%s) — falling back to keyword search", SEMANTIC_RPC, exc)
        return _keyword_search_jobs(db, query, limit)

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


def _load_work_auth(user_id: str) -> dict:
    """The user's `work_authorizations` map, for the geo score cap.

    Mirrors `score_batch.handler` exactly, including failing open to `{}` and
    logging: a profile read that hiccups must not fail a score. `{}` still
    leaves the non-home-country cap in force (an unknown sponsorship
    requirement is not a known absence of one), so the worst case of a failed
    read is A-tier instead of B-tier, never S-tier.
    """
    try:
        row = (
            _db()
            .table("users")
            .select("work_authorizations,location")
            .eq("id", user_id)
            .single()
            .execute()
            .data
        ) or {}
    except Exception as exc:
        logger.warning("[mcp] could not load work_authorizations for %s: %s", user_id, exc)
        return {}
    return row.get("work_authorizations") or {}


async def score_job(
    jd_text: str,
    title: str = "",
    company: str = "",
    location: str | None = None,
    remote: str | None = None,
    resume_tex: str | None = None,
) -> dict:
    """Score a job description against the candidate's base resume.

    Returns the same numbers, under the same names, that the REST rebuild path
    (`app._score_rebuilt_resume`) returns for the same job — the four scores
    and the `score_spread` band — plus the tier, the reasoning and the gaps.

    `title`, `company`, `location` and `remote` are all part of the scoring
    prompt, so omitting them produces a *worse* score than the batch pipeline
    gets for the same job, not a neutral one. `location` additionally decides
    the geography / work-authorisation cap: this tool used to send none, so
    `apply_geo_score_cap` could not fire and a US role needing sponsorship the
    candidate cannot get came back S-tier where the pipeline records B.

    `resume_tex` is optional — when omitted, the latest resume on file for
    the single known user (DEFAULT_USER_ID) is loaded from `user_resumes`,
    the same source `/api/score` reads. It is never defaulted to an empty
    string.

    Both `jd_text` and `resume_tex` arrive from the MCP client, so they are
    scored through `untrusted_input=True`: injection-checked (the call is
    refused, not silently scored), PII-scrubbed and fenced. See
    `score_batch._guard_untrusted_scoring_input`.
    """
    if resume_tex is None:
        resume_tex = _load_base_resume(DEFAULT_USER_ID)

    job = {
        "job_hash": "mcp:score_job",
        "title": title,
        "company": company,
        "description": jd_text,
        "location": location or "",
        "remote": remote,
    }
    result = (
        score_single_job_deterministic(
            job,
            resume_tex,
            num_calls=SCORE_CALLS,
            skip_cache=True,
            untrusted_input=True,
        )
        or {}
    )
    # The same post-hoc cap score_batch.handler applies to every row it writes.
    # Without it this tool and the dashboard disagree about the same job.
    result = apply_geo_score_cap(result, job, _load_work_auth(DEFAULT_USER_ID))

    match_score = result.get("match_score", 0.0)
    return {
        "match_score": match_score,
        "tier": score_to_tier(match_score),
        "ats_score": result.get("ats_score"),
        "hiring_manager_score": result.get("hiring_manager_score"),
        "tech_recruiter_score": result.get("tech_recruiter_score"),
        "score_spread": result.get("score_spread"),
        "gaps": result.get("gaps", []),
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


def _transport_security() -> TransportSecuritySettings:
    """Allowed Host/Origin values for the HTTP transport.

    Read from `MCP_ALLOWED_HOSTS` (comma-separated) at build time rather than
    at import time — CLAUDE.md verification rule 8, configuration must be
    explicit and not an import side effect. Localhost is always allowed so a
    developer running `uvicorn app:app` keeps working; everything else has to
    be named. An unset variable therefore fails closed to localhost-only,
    which is exactly the pre-existing behaviour, not a widening.
    """
    named = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    hosts = [*LOCAL_ALLOWED_HOSTS, *named]
    origins = [f"http://{h}" for h in LOCAL_ALLOWED_HOSTS] + [f"https://{h}" for h in named]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_server() -> FastMCP:
    mcp = FastMCP("naukribaba", transport_security=_transport_security())
    mcp.tool()(search_jobs)
    mcp.tool()(score_job)
    mcp.tool()(get_job)
    return mcp


if __name__ == "__main__":
    # stdio. This is the transport a local Claude Code / Claude Desktop entry
    # uses; see docs/mcp-client-setup.md for the config block and for why the
    # deployed SSE endpoint needs one more piece of infrastructure before a
    # remote client can complete a handshake through it.
    build_server().run()
