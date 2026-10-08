-- jobs.critique_outcome — document every value the pipeline now writes.
--
-- 20260930020000_jobs_critique_outcome.sql listed five values, all of them
-- council outcomes. Since 2026-10-08 tailor_resume records what produced the
-- document that SHIPPED, not what the council did with a document that was
-- then discarded, so three more values reach this column. That migration is
-- left unedited (it is already applied); this one replaces the column comment.
--
-- Comment only. The column is plain TEXT with NO CHECK constraint (verified:
-- no migration constrains it), so the new values were never rejected and no
-- constraint change is needed.
--
-- Values, and who writes them:
--
--   adjudicated          council: a cross-family critic scored the candidates
--   single_candidate     council: one candidate survived generation
--   no_critic_family     council: every live family had generated, so no
--                        independent critic existed and none was called. Both
--                        engines (agents/providers.select_critics and
--                        ai_helper._council_complete_legacy) now record this
--                        instead of borrowing a same-family critic, so rows
--                        that would have read "adjudicated" read this instead.
--   critic_call_failed   council: the critic returned nothing, or raised
--   critic_unparseable   council: the critic answered in an unreadable shape
--   unknown              the council result carried no outcome
--                        (agents/graph.py default)
--   single_call_retry    tailor_resume: a corrective/quality retry or a
--                        composition repair shipped -- one model, no critic
--   corpus_fallback      tailor_resume: the corpus shipped; no model wrote it
--
-- Only "adjudicated" means a reviewed document. Every other value is an
-- unreviewed one, so an adjudication rate is
--   count(*) filter (where critique_outcome = 'adjudicated') / count(*)
-- over rows where critique_outcome is not null.

COMMENT ON COLUMN public.jobs.critique_outcome IS
  'What produced the shipped artifact. Council: adjudicated, single_candidate, '
  'no_critic_family, critic_call_failed, critic_unparseable, unknown. '
  'Not the council: single_call_retry (one-model retry or repair shipped), '
  'corpus_fallback (the corpus shipped). Only adjudicated was reviewed by a '
  'cross-family critic. See CRITIQUE_OUTCOMES in lambdas/pipeline/agents/nodes.py '
  'and the shipped_from logic in lambdas/pipeline/tailor_resume.py.';
