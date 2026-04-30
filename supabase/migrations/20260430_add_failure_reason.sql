-- Add failure_reason column to surface compile_latex / tailoring failures.
--
-- Bug X1 (Phase A.3): compile_latex returned error dicts on tectonic failure;
-- save_job silently set application_status='scored' with no resume_s3_url, so the
-- job appeared in the dashboard with a score but no PDF. The save_job change
-- writes application_status='failed' + failure_reason here when compile fails.

BEGIN;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason TEXT;

-- Partial index for the dashboard "show only failed" filter and for ops audits.
CREATE INDEX IF NOT EXISTS idx_jobs_failed
  ON jobs (user_id, last_seen DESC)
  WHERE application_status = 'failed';

COMMIT;
