-- Semantic job search RPC for the MCP `search_jobs` tool (Task 26).
--
-- Scoped by p_user_id for the same reason match_jobs_in_company (Task 13,
-- 20260922000100_pgvector_rpcs.sql) scopes by company: this repo is
-- single-tenant in practice today, but the `jobs` table itself is
-- multi-tenant (user_id NOT NULL), and an MCP client has no per-call
-- identity of its own to otherwise limit it by (see mcp_server/server.py's
-- DEFAULT_USER_ID). Unscoped, this RPC would let any connected client read
-- ranked titles across every tenant's private job-hunt data.
--
-- `create or replace function` is idempotent, so re-running this file
-- against an already-migrated database is a no-op — same convention as
-- 20260922000100_pgvector_rpcs.sql.
--
-- NOT APPLIED as part of this change. This repo's `supabase db push` is
-- currently blocked by three pre-existing duplicate migration version
-- prefixes (20260430_add_failure_reason.sql, 20260430_add_posted_date_
-- jobs_raw.sql, and 20260430_resume_versions_unique.sql all share the
-- "20260430" prefix with no time component, which the Supabase CLI's
-- version ordering can't disambiguate), so migrations currently go through
-- the Supabase SQL editor in the dashboard instead. Until this file is
-- applied there, `mcp_server.server.search_jobs` catches this RPC's
-- "function not found" error and falls back to a live keyword search over
-- the same `jobs` table — see that module for details.

BEGIN;

create or replace function public.match_jobs_semantic(
  p_user_id uuid, p_embedding vector(768), p_k int
) returns table (
  job_hash text, title text, company text,
  match_score real, score_tier text, similarity float
)
language sql stable as $$
  select j.job_hash, j.title, j.company, j.match_score, j.score_tier,
         1 - (j.embedding <=> p_embedding) as similarity
  from public.jobs j
  where j.user_id = p_user_id
    and j.embedding is not null
  order by j.embedding <=> p_embedding
  limit p_k;
$$;

COMMIT;
