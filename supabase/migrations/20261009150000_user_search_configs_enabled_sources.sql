-- user_search_configs.enabled_sources: the Settings -> Job Sources toggles.
--
-- Verified missing in production 2026-10-09 (read-only select):
--   400 {"code":"42703","message":"column user_search_configs.enabled_sources does not exist"}
-- The earlier 20260506_user_search_configs_enabled_sources.sql was never
-- applied. Until this is, PUT /api/search-config cannot store the field and
-- now says so (HTTP 409) instead of answering "Job sources saved.".
--
-- Nullable with no default, deliberately. NULL means "never chosen", which the
-- UI renders as its own defaults and the pipeline (which does not filter by
-- source yet) treats as all sources. The 20260506 file instead defaulted every
-- row to a hard-coded list naming 'hn_hiring', while the UI's id is 'hn', so
-- applying it would have switched HN off for every user. The ALTERs below
-- converge to the same end state whether or not that file was applied first.
--
-- Idempotent; safe to run more than once. NOT APPLIED by the commit that adds it.

alter table public.user_search_configs
  add column if not exists enabled_sources jsonb;

alter table public.user_search_configs
  alter column enabled_sources drop not null;

alter table public.user_search_configs
  alter column enabled_sources drop default;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'user_search_configs_enabled_sources_is_array'
  ) then
    alter table public.user_search_configs
      add constraint user_search_configs_enabled_sources_is_array
      check (enabled_sources is null or jsonb_typeof(enabled_sources) = 'array');
  end if;
end $$;

-- PostgREST caches the schema; without this the new column answers PGRST204
-- ("Could not find the 'enabled_sources' column ... in the schema cache").
notify pgrst, 'reload schema';
