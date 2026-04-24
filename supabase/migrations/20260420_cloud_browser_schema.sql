-- Cloud browser schema additions for applications table
-- Expands submission_method to include 'cloud_browser',
-- adds browser session tracking and form field diagnostics

BEGIN;

-- 1. Drop and recreate CHECK constraint to add 'cloud_browser'
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_submission_method_check;
ALTER TABLE applications ADD CONSTRAINT applications_submission_method_check
  CHECK (submission_method IN (
    'greenhouse_api', 'ashby_api', 'remote_browser', 'assisted_manual', 'cloud_browser'
  ));

-- 2. Add browser session tracking column
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS browser_session_id UUID;

-- 3. Add form field diagnostics
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS form_fields_detected INT,
  ADD COLUMN IF NOT EXISTS form_fields_filled INT;

COMMIT;
