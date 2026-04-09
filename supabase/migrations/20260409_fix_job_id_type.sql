-- Fix job_id column type in application_timeline and resume_versions.
-- The jobs table uses TEXT for job_id (12-char hex hashes), but these tables
-- were created with UUID type, causing insert failures (500 errors).

ALTER TABLE public.application_timeline
  ALTER COLUMN job_id TYPE TEXT USING job_id::TEXT;

ALTER TABLE public.resume_versions
  ALTER COLUMN job_id TYPE TEXT USING job_id::TEXT;
