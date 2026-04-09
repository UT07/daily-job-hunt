-- Add posted_date column to jobs table.
-- Stores the date the job was originally posted by the company (as reported
-- by the job board), distinct from first_seen which is when we scraped it.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS posted_date timestamptz;

-- Index for sorting by posted_date
CREATE INDEX IF NOT EXISTS idx_jobs_posted_date
  ON public.jobs (user_id, posted_date)
  WHERE posted_date IS NOT NULL;
