-- Phase 2.5: scrape_runs table for Fargate → Step Functions output contract
-- Each scraper (Lambda or Fargate) writes a summary row here

CREATE TABLE IF NOT EXISTS scrape_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed', 'blocked')),
    jobs_found INTEGER DEFAULT 0,
    jobs_new INTEGER DEFAULT 0,
    new_job_hashes JSONB DEFAULT '[]'::jsonb,
    error_message TEXT,
    blocked_reason TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_scrape_runs_pipeline ON scrape_runs(pipeline_run_id);
CREATE INDEX idx_scrape_runs_source ON scrape_runs(source, started_at DESC);

-- Add description_quality column to jobs_raw if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'jobs_raw' AND column_name = 'description_quality'
    ) THEN
        ALTER TABLE jobs_raw ADD COLUMN description_quality TEXT DEFAULT 'full';
    END IF;
END $$;
