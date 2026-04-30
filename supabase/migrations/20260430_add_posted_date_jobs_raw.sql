-- Add posted_date column to jobs_raw so scrapers can record when each
-- posting was originally listed by its source.
--
-- jobs already has posted_date (added 20260409_add_posted_date.sql) but
-- jobs_raw didn't, which meant merge_dedup's freshness pre-filter had no
-- signal to read. This migration plugs that gap.
--
-- Companion change: lambdas/scrapers/normalizers.py + per-source
-- scrapers (linkedin, indeed, adzuna, hn, greenhouse, ashby) now
-- populate posted_date when the source provides it. Sources that don't
-- expose a posting timestamp leave the column NULL — the freshness
-- filter passes NULL through (won't reject for missing data).

BEGIN;

ALTER TABLE jobs_raw ADD COLUMN IF NOT EXISTS posted_date TIMESTAMPTZ;

-- Index for the merge_dedup freshness scan (last-N-days windows).
CREATE INDEX IF NOT EXISTS idx_jobs_raw_posted_date
  ON jobs_raw (posted_date DESC NULLS LAST)
  WHERE posted_date IS NOT NULL;

COMMIT;
