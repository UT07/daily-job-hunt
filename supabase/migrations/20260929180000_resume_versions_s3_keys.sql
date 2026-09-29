-- resume_versions stored URLs, so a "version" pointed at nothing durable.
--
-- Two independent reasons a restore could not work:
--
--   1. The URLs are presigned and expire after 7 days. Nothing in the row
--      recorded the S3 key, so there was no way to mint a fresh one. Same
--      defect as jobs.cover_letter_s3_key (migration 20260929120000).
--
--   2. Worse: the object itself was gone. Artifacts are keyed
--      users/{uid}/resumes/{job_hash}_tailored.pdf with no version component,
--      and the bucket has versioning DISABLED (verified 2026-09-29:
--      get-bucket-versioning returns empty). Regenerating overwrote the object
--      in place. A job at resume_version=2 has exactly one .pdf and one .tex.
--
-- So restore_resume_version copied a stale URL back onto the job, the URL
-- resolved to the CURRENT document, and the user got the same resume back
-- relabelled as an older version. It reported success every time.
--
-- The fix archives the live objects to
-- users/{uid}/resumes/versions/{job_hash}_v{n}_tailored.pdf before a
-- regenerate overwrites them, and records those keys here. The live key is
-- unchanged, so every existing reader keeps working.
--
-- Existing rows cannot be backfilled: the bytes they described were
-- overwritten and no key was ever recorded. They stay NULL and the API
-- reports them as unrestorable rather than pretending.

BEGIN;

ALTER TABLE resume_versions
  ADD COLUMN IF NOT EXISTS resume_s3_key TEXT,
  ADD COLUMN IF NOT EXISTS cover_letter_s3_key TEXT;

COMMENT ON COLUMN resume_versions.resume_s3_key IS
  'Archived copy of the resume PDF as it was at this version, under '
  'resumes/versions/. NULL for rows created before 2026-09-29, whose '
  'artifacts were overwritten in place and cannot be recovered.';

COMMENT ON COLUMN resume_versions.cover_letter_s3_key IS
  'Archived copy of the cover-letter PDF at this version. See resume_s3_key.';

COMMIT;
