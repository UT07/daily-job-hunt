-- jobs.cover_letter_s3_key — the column _refresh_s3_urls has always read and
-- that has never existed.
--
-- app.py's _refresh_s3_urls re-signs both artifact URLs on every dashboard
-- read:
--
--     ("resume_s3_key",       "resume_s3_url",       "resume")
--     ("cover_letter_s3_key", "cover_letter_s3_url", "cover_letter")
--
-- The resume half works. The cover-letter half reads a column that is not on
-- the table, so job.get(...) is always None, the `if s3_key:` guard is always
-- false, and the URL is never re-signed. Presigned URLs expire after 7 days, so
-- every cover-letter link dies and nothing can ever mint a new one — the
-- dashboard keeps showing a button that 403s.
--
-- Measured 2026-09-29: 400 jobs carried a cover_letter_s3_url; the key column
-- did not exist. `cover_letter_pdf_path` is not a substitute — it is set on 7
-- of those 400 and holds a bare filename
-- ("Utkarsh_Singh_..._CoverLetter.pdf"), not an S3 key.
--
-- save_job.py now writes this key alongside the URL, mirroring what it already
-- does for the resume. That code tolerates this column being absent (it drops
-- the column named in a PGRST204 and retries), so applying this migration
-- before or after the deploy is safe either way. Backfill is not possible for
-- existing rows — the key was never recorded anywhere — so their links stay
-- dead until the job is regenerated.

BEGIN;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS cover_letter_s3_key TEXT;

COMMENT ON COLUMN jobs.cover_letter_s3_key IS
  'S3 key of the compiled cover-letter PDF. Written by save_job alongside '
  'cover_letter_s3_url so _refresh_s3_urls can re-sign the URL before it '
  'expires. Mirrors resume_s3_key.';

COMMIT;
