-- jobs.critique_outcome — whether the council actually adjudicated this result.
--
-- critique_node has four paths that return candidates[0] without a verdict,
-- and until 2026-09-30 they were indistinguishable from a real adjudication:
-- the return shape differed only by `scores` being empty, and one of the four
-- logged nothing at all.
--
-- Measured over 80 rounds: the critic produced a usable verdict in 21. The
-- other 59 took candidate 1 unreviewed. 26% adjudication, discovered only by
-- grepping CloudWatch for log strings.
--
-- Values (agents/nodes.py CRITIQUE_OUTCOMES):
--
--   adjudicated          a critic scored the candidates and one won
--   single_candidate     only one survived generation; nothing to compare
--   no_critic_family     no provider available to critique at all
--   critic_call_failed   the critic provider returned nothing, or raised
--   critic_unparseable   the critic answered in a shape we cannot read
--
-- With this column the rate is one SQL query instead of a log-archaeology
-- session, which is what Phase 2 of the agent-orchestration spec is judged by.
--
-- Nullable: rows written before this migration have no outcome to recover.
-- tailor_resume drops the column and retries when it is absent, so applying
-- this before or after the deploy is safe either way.

BEGIN;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS critique_outcome TEXT;

COMMENT ON COLUMN jobs.critique_outcome IS
  'Whether the AI council adjudicated this artifact or fell back to the first '
  'candidate. See CRITIQUE_OUTCOMES in lambdas/pipeline/agents/nodes.py.';

COMMIT;
