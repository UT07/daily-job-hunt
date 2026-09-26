-- Adds jobs.trace_id (Task 10, 2026-09-22 GenAI platform upgrade plan).
--
-- Links a scored job row back to the LangGraph council run (thread_id,
-- and -- once LangSmith tracing is confirmed live -- the matching LangSmith
-- thread) that produced it, when the AI call that scored it actually went
-- through the council and returned one.
--
-- Nullable, no default, no backfill: every existing row predates this
-- column, and most scoring calls today go through score_single_job's
-- single-model ai_complete_cached path (lambdas/pipeline/score_batch.py),
-- which never produces a trace_id -- only council_complete_langgraph does.
-- This column is forward-compatible plumbing (score_batch.py already writes
-- score_result.get("trace_id") into it, defensively dropped on insert if
-- this migration has not been applied yet -- see its retry-without-optional-
-- columns handling), not a claim that every row will have one.
--
-- Idempotent (IF NOT EXISTS), matching the convention in
-- 20260405_add_score_tier.sql and 20260922000000_pgvector.sql, so
-- re-running this file against an already-migrated database is a no-op.

alter table public.jobs
  add column if not exists trace_id text;
