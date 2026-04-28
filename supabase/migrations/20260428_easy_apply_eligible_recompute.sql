-- Recompute easy_apply_eligible to match the API gate flipped in PR #10 (Apr 27).
-- Previous formula: (apply_platform IS NOT NULL) — under-counted by ~600 rows.
-- New formula: (apply_url IS NOT NULL AND resume_s3_key IS NOT NULL).
-- The actual /api/apply/eligibility endpoint also checks profile completeness and
-- already_applied, but those are user-state-dependent and can't live in a job-level
-- generated column. The column is a "could this job ever be eligible?" hint, not
-- a per-user verdict.

BEGIN;

-- Drop the dependent index first
DROP INDEX IF EXISTS idx_jobs_easy_apply_eligible;

-- Drop the old generated column
ALTER TABLE jobs DROP COLUMN IF EXISTS easy_apply_eligible;

-- Re-add with the correct formula
ALTER TABLE jobs
  ADD COLUMN easy_apply_eligible BOOLEAN
  GENERATED ALWAYS AS (apply_url IS NOT NULL AND resume_s3_key IS NOT NULL) STORED;

-- Recreate the index that supports the dashboard query path
CREATE INDEX idx_jobs_easy_apply_eligible
  ON jobs(user_id, score_tier, easy_apply_eligible)
  WHERE is_expired = false;

COMMIT;
