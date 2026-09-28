-- Reclaim jobs.application_status for the user.
--
-- application_status is the USER's column: what they did with a job
-- (New / Applied / Phone Screen / Interview / Offer / Rejected / Withdrawn /
-- Accepted). The pipeline used to write 'ready', 'scored' and 'failed' into
-- it -- pipeline states, not user actions. Two consequences, both measured in
-- production on 2026-09-28:
--
--   1. Those values are not in app.py's _VALID_STATUSES, so the API would
--      reject a user setting them while the pipeline wrote them freely. The
--      dashboard's Status filter could never match a row holding one --
--      1,097 of 1,268 rows, or 87% of the table.
--   2. Writing one ERASED a real user status. A job the user had marked
--      "Applied" reverted to "ready" on the next pipeline run.
--
-- The writer was removed in lambdas/pipeline/save_job.py (it now writes
-- nothing rather than writing a pipeline state into a user column). This
-- migration cleans up the rows that writer left behind, and adds the
-- constraint that stops the class of bug returning.
--
-- NOTHING IS LOST. Each pipeline state is derivable from a column that still
-- holds it, and that was verified row-by-row across all 1,097 before this
-- migration was written -- zero exceptions:
--
--   ready  (794)  -> resume_s3_url  IS NOT NULL
--   scored (294)  -> match_score    IS NOT NULL
--   failed   (9)  -> failure_reason IS NOT NULL
--
-- ROLLBACK: these rows become indistinguishable from genuinely-new ones, so
-- restoring the exact prior values needs the backup taken alongside this
-- migration. The states themselves remain derivable from the three columns
-- above regardless.

BEGIN;

-- 1. Reset pipeline states to the user-facing default.
--    Idempotent: re-running matches nothing.
UPDATE jobs
   SET application_status = 'New'
 WHERE application_status IN ('ready', 'scored', 'failed');

-- 2. Anything else unexpected (NULL, or a value from some older writer) also
--    becomes 'New'. The column is meant to always hold a user-facing state.
UPDATE jobs
   SET application_status = 'New'
 WHERE application_status IS NULL
    OR application_status NOT IN (
        'New', 'Applied', 'Phone Screen', 'Interview',
        'Offer', 'Rejected', 'Withdrawn', 'Accepted'
    );

-- 3. Make the invalid state unrepresentable.
--    Every writer already validates against app.py's _VALID_STATUSES
--    (app.py:1804 and app.py:1929, both raising HTTP 400), so this constraint
--    should never fire in normal operation. It exists so that the next writer
--    added to this table fails loudly at the boundary instead of silently
--    corrupting 87% of the rows again.
ALTER TABLE jobs
  DROP CONSTRAINT IF EXISTS jobs_application_status_valid;

ALTER TABLE jobs
  ADD CONSTRAINT jobs_application_status_valid
  CHECK (application_status IN (
      'New', 'Applied', 'Phone Screen', 'Interview',
      'Offer', 'Rejected', 'Withdrawn', 'Accepted'
  ));

COMMENT ON COLUMN jobs.application_status IS
  'The USER''s state for this job, never the pipeline''s. Pipeline state is '
  'derived: ready = resume_s3_url IS NOT NULL, scored = match_score IS NOT '
  'NULL, failed = failure_reason IS NOT NULL. Constrained by '
  'jobs_application_status_valid; keep in sync with app.py _VALID_STATUSES.';

COMMIT;
