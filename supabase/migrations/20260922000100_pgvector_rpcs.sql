-- RPC functions for pgvector similarity queries (Task 13).
--
-- Split from 20260922000000_pgvector.sql (Task 11) into its own migration: the
-- Supabase CLI records applied migrations by version and skips ones already
-- recorded, so SQL appended to a migration file after it has been pushed
-- would never reach the database. `create or replace function` is already
-- idempotent, so re-running this file against an already-migrated database
-- is a no-op.

BEGIN;

-- Jobs at the same company whose description embedding is near p_embedding.
-- Scoped by company because two genuinely different roles at different
-- employers can have near-identical descriptions.
create or replace function public.match_jobs_in_company(
  p_company text, p_embedding vector(768), p_threshold float
) returns table (job_hash text, title text, similarity float)
language sql stable as $$
  select j.job_hash, j.title, 1 - (j.embedding <=> p_embedding) as similarity
  from public.jobs j
  where j.company = p_company
    and j.embedding is not null
    and 1 - (j.embedding <=> p_embedding) >= p_threshold
  order by j.embedding <=> p_embedding
  limit 20;
$$;

-- Top-k of a user's own resume bullets, nearest first, for retrieval-grounded
-- tailoring.
create or replace function public.match_resume_bullets(
  p_user_id uuid, p_embedding vector(768), p_k int
) returns table (id uuid, section text, text text, similarity float)
language sql stable as $$
  select b.id, b.section, b.text, 1 - (b.embedding <=> p_embedding) as similarity
  from public.resume_bullets b
  where b.user_id = p_user_id and b.embedding is not null
  order by b.embedding <=> p_embedding
  limit p_k;
$$;

COMMIT;
