-- Close pipeline_tasks and scrape_runs to the public API keys.
--
-- Found by audit 2026-10-08 in 00000000000000_initial_schema.sql:
--
--   * CREATE POLICY "Service role full access" ON pipeline_tasks USING (true)
--     has no FOR and no TO clause, so Postgres applies it to ALL commands for
--     PUBLIC. The name says service role; the policy says everyone. Together
--     with GRANT ALL ... TO anon, anyone holding the anon key (it ships in the
--     web bundle) could read every user's task payloads and results through
--     PostgREST, and insert, update or delete them.
--   * scrape_runs never had RLS enabled, and is also GRANT ALL to anon.
--
-- Who actually uses these tables: only the backend (app.py, merge_dedup.py,
-- scrapers), always with the service key. service_role has BYPASSRLS, so it
-- needs no policy. Nothing in web/src queries either table directly, so
-- authenticated needs no policy and no grant either. If the frontend ever
-- does, add a FOR SELECT TO authenticated USING (auth.uid() = user_id)
-- policy in a new migration; tests/unit/test_rls_internal_tables.py will
-- fail until it is scoped.
--
-- Idempotent: every statement can be re-run.

DROP POLICY IF EXISTS "Service role full access" ON public.pipeline_tasks;
ALTER TABLE public.pipeline_tasks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pipeline_tasks FROM anon;
REVOKE ALL ON TABLE public.pipeline_tasks FROM authenticated;
GRANT ALL ON TABLE public.pipeline_tasks TO service_role;

ALTER TABLE public.scrape_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.scrape_runs FROM anon;
REVOKE ALL ON TABLE public.scrape_runs FROM authenticated;
GRANT ALL ON TABLE public.scrape_runs TO service_role;
