-- ========== Auto-Apply Setup — Mode 1 + Cloud Browser ==========
-- Adds applications table, new user/job columns, platform eligibility

BEGIN;

-- 1. set_updated_at() trigger helper (idempotent)
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. users: split name into first/last, add referral source
-- NOTE: salary_expectation_notes, notice_period_text, onboarding_completed_at
--       already exist from 20260412_onboarding_profile.sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS first_name TEXT,
  ADD COLUMN IF NOT EXISTS last_name TEXT,
  ADD COLUMN IF NOT EXISTS default_referral_source TEXT DEFAULT 'LinkedIn';

-- Ensure notice_period_text has a default (column exists from 20260412 migration but without default)
ALTER TABLE users ALTER COLUMN notice_period_text SET DEFAULT 'Available in 2 weeks';
UPDATE users SET notice_period_text = 'Available in 2 weeks' WHERE notice_period_text IS NULL;

-- 3. Backfill first/last_name from existing name column
UPDATE users SET
  first_name = split_part(name, ' ', 1),
  last_name  = CASE
    WHEN position(' ' in name) > 0
      THEN substring(name from position(' ' in name) + 1)
    ELSE ''
  END
WHERE first_name IS NULL AND name IS NOT NULL;

-- 4. Populate profile defaults for the existing user
UPDATE users SET
  location = COALESCE(NULLIF(location, ''), 'Dublin, Ireland'),
  visa_status = COALESCE(NULLIF(visa_status, ''), 'Stamp 1G'),
  work_authorizations = CASE
    WHEN work_authorizations IS NULL OR work_authorizations = '{}'::jsonb
      THEN '{"IE": "authorized", "UK": "requires_visa", "US": "requires_sponsorship", "EU": "requires_visa"}'::jsonb
    ELSE work_authorizations
  END
WHERE id = '7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39';

-- 5. jobs: add apply_* columns + generated eligibility flag
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS apply_platform TEXT,
  ADD COLUMN IF NOT EXISTS apply_board_token TEXT,
  ADD COLUMN IF NOT EXISTS apply_posting_id TEXT;

-- Generated column must be added separately (can't combine with IF NOT EXISTS on other cols)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'jobs' AND column_name = 'easy_apply_eligible'
  ) THEN
    ALTER TABLE jobs ADD COLUMN easy_apply_eligible BOOLEAN
      GENERATED ALWAYS AS (apply_platform IS NOT NULL) STORED;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_jobs_easy_apply_eligible
  ON jobs(user_id, score_tier, easy_apply_eligible)
  WHERE is_expired = false;

-- 6. applications table
CREATE TABLE IF NOT EXISTS applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,  -- no FK: jobs has composite PK (job_id, user_id), can't target single column
  job_hash TEXT NOT NULL,
  canonical_hash TEXT,
  submission_method TEXT NOT NULL CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api', 'remote_browser', 'assisted_manual'
  )),
  platform TEXT NOT NULL,
  posting_id TEXT,
  board_token TEXT,
  resume_s3_key TEXT NOT NULL,
  resume_version INT NOT NULL DEFAULT 1,
  resume_is_default BOOLEAN NOT NULL DEFAULT FALSE,
  cover_letter_text TEXT,
  include_cover_letter BOOLEAN NOT NULL DEFAULT FALSE,
  answers JSONB NOT NULL DEFAULT '[]',
  profile_snapshot JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN (
    'submitted', 'unknown', 'confirmed', 'viewed',
    'rejected', 'interview', 'offer', 'ghosted', 'failed'
  )),
  platform_response JSONB,
  confirmation_screenshot_s3_key TEXT,
  dry_run BOOLEAN NOT NULL DEFAULT FALSE,
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  response_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unique constraint: one active application per canonical job (excl. dry runs and retryable statuses)
CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_one_active_per_canonical
  ON applications(user_id, canonical_hash)
  WHERE status NOT IN ('unknown', 'failed')
    AND canonical_hash IS NOT NULL
    AND dry_run = false;

CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_one_active_per_job
  ON applications(user_id, job_id)
  WHERE status NOT IN ('unknown', 'failed')
    AND dry_run = false;

CREATE INDEX IF NOT EXISTS idx_applications_user_status ON applications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_submitted ON applications(submitted_at DESC);

-- RLS
ALTER TABLE applications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users read own applications" ON applications;
CREATE POLICY "Users read own applications" ON applications
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert own applications" ON applications;
CREATE POLICY "Users insert own applications" ON applications
  FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users update own applications" ON applications;
CREATE POLICY "Users update own applications" ON applications
  FOR UPDATE USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Service role full access applications" ON applications;
CREATE POLICY "Service role full access applications" ON applications
  FOR ALL USING (auth.role() = 'service_role');

-- updated_at trigger
DROP TRIGGER IF EXISTS trg_applications_updated_at ON applications;
CREATE TRIGGER trg_applications_updated_at
  BEFORE UPDATE ON applications
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
