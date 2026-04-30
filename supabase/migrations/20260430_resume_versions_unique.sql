-- Add UNIQUE(user_id, job_id, version_number) on resume_versions.
--
-- Phase A.1.5: root cause of "multiple v1 entries". The original migration
-- (008_resume_versions.sql) only set DEFAULT 1 on version_number with no
-- uniqueness, so concurrent re-tailors could write multiple rows with the
-- same (user_id, job_id, version_number=1).
--
-- Strategy: dedupe-then-constrain. Within each (user_id, job_id,
-- version_number) bucket we keep the most recently created row and delete
-- the rest. The dedup CTE is run in the same transaction as the constraint
-- so we can't end up with new dupes between dedup and constraint.

BEGIN;

-- 1) Delete duplicate rows, keeping the newest per (user_id, job_id, version_number).
WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY user_id, job_id, version_number
      ORDER BY created_at DESC, id DESC
    ) AS rn
  FROM public.resume_versions
)
DELETE FROM public.resume_versions
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 2) Add the constraint. Future inserts of a duplicate trio will fail loudly
--    instead of silently appending a second v1.
ALTER TABLE public.resume_versions
  ADD CONSTRAINT resume_versions_user_job_version_unique
  UNIQUE (user_id, job_id, version_number);

COMMIT;
