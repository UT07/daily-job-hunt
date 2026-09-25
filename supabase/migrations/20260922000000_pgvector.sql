-- pgvector support for semantic dedup and bullet retrieval.
--
-- 768 dimensions matches Gemini text-embedding-004. Chosen over 1536 (OpenAI)
-- to halve index size and HNSW probe cost; recall on a corpus this size does
-- not justify the larger vector.
--
-- jobs.embedding is nullable with no default: the 1,243 existing rows are
-- backfilled by a separate script (later task), not by this migration.
-- Every statement below is idempotent (IF NOT EXISTS / DROP ... IF EXISTS)
-- so re-running this file against an already-migrated database is a no-op.
--
-- Scope: schema only, for jobs + resume_bullets. No RPC functions here —
-- those land in their own migration (Task 13) so the Supabase CLI's
-- per-migration version tracking can't silently skip SQL appended to this
-- file after it has already been pushed.

BEGIN;

create extension if not exists vector;

-- Semantic dedup target: nullable, backfilled later.
alter table public.jobs
  add column if not exists embedding vector(768);

-- Candidate's own resume bullets, embedded for retrieval-grounded tailoring.
create table if not exists public.resume_bullets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  section text not null,
  text text not null,
  embedding vector(768),
  source_resume_id uuid,
  created_at timestamptz not null default now()
);

create index if not exists jobs_embedding_hnsw
  on public.jobs using hnsw (embedding vector_cosine_ops);

create index if not exists resume_bullets_embedding_hnsw
  on public.resume_bullets using hnsw (embedding vector_cosine_ops);

create index if not exists resume_bullets_user_id_idx
  on public.resume_bullets (user_id);

-- RLS: same per-user ownership pattern used throughout (jobs, user_resumes,
-- user_search_configs, runs, applications, ...). CREATE POLICY has no
-- IF NOT EXISTS form, so each policy is dropped before being recreated.
alter table public.resume_bullets enable row level security;

drop policy if exists resume_bullets_owner_select on public.resume_bullets;
create policy resume_bullets_owner_select on public.resume_bullets
  for select using (auth.uid() = user_id);

drop policy if exists resume_bullets_owner_insert on public.resume_bullets;
create policy resume_bullets_owner_insert on public.resume_bullets
  for insert with check (auth.uid() = user_id);

drop policy if exists resume_bullets_owner_update on public.resume_bullets;
create policy resume_bullets_owner_update on public.resume_bullets
  for update using (auth.uid() = user_id);

drop policy if exists resume_bullets_owner_delete on public.resume_bullets;
create policy resume_bullets_owner_delete on public.resume_bullets
  for delete using (auth.uid() = user_id);

COMMIT;
