-- ============================================================
--  Migration 003: Phase 2E — Step Functions Pipeline Tables
--  Version: 003
--  Created: 2026-03-31
--  Description: Tables for Step Functions pipeline migration:
--               jobs_raw (shared scrape data), ai_cache (AI response cache),
--               self_improvement_config (per-user tuning), pipeline_metrics (run stats)
--               + new columns on jobs and users tables
-- ============================================================

-- ============================================================
--  jobs_raw: Shared raw job data (scraped once, scored per-user)
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs_raw (
  job_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  description TEXT,
  location TEXT,
  apply_url TEXT,
  source TEXT NOT NULL,
  experience_level TEXT,
  job_type TEXT,
  query_hash TEXT,
  scraped_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_raw_source_query ON jobs_raw(source, query_hash, scraped_at);
CREATE INDEX IF NOT EXISTS idx_jobs_raw_scraped ON jobs_raw(scraped_at);

ALTER TABLE jobs_raw ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can read jobs_raw" ON jobs_raw FOR SELECT USING (true);
CREATE POLICY "Service role writes jobs_raw" ON jobs_raw FOR ALL USING (auth.role() = 'service_role');


-- ============================================================
--  ai_cache: AI response cache (replaces SQLite .ai_cache.db)
-- ============================================================

CREATE TABLE IF NOT EXISTS ai_cache (
  cache_key TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_cache_expires ON ai_cache(expires_at);

ALTER TABLE ai_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role only" ON ai_cache FOR ALL USING (auth.role() = 'service_role');


-- ============================================================
--  self_improvement_config: Per-user AI tuning adjustments
-- ============================================================

CREATE TABLE IF NOT EXISTS self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL,
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

ALTER TABLE self_improvement_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own config" ON self_improvement_config FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON self_improvement_config FOR ALL USING (auth.role() = 'service_role');


-- ============================================================
--  pipeline_metrics: Per-run, per-scraper metrics
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_metrics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  run_date DATE NOT NULL,
  execution_id TEXT,
  scraper_name TEXT NOT NULL,
  jobs_found INT DEFAULT 0,
  jobs_matched INT DEFAULT 0,
  jobs_tailored INT DEFAULT 0,
  duration_seconds INT,
  apify_cost_cents INT DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_user_date ON pipeline_metrics(user_id, run_date);

ALTER TABLE pipeline_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own metrics" ON pipeline_metrics FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON pipeline_metrics FOR ALL USING (auth.role() = 'service_role');


-- ============================================================
--  Alter existing tables: add new columns
-- ============================================================

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_hash TEXT REFERENCES jobs_raw(job_hash);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_version INT DEFAULT 1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT false;

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_pipeline_run TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs JSONB DEFAULT '{"email": true, "sms": false, "whatsapp": false}';


-- ============================================================
--  Data migration: Backfill jobs_raw from existing jobs
-- ============================================================

INSERT INTO jobs_raw (job_hash, title, company, description, location, apply_url, source, scraped_at)
SELECT
  md5(company || '|' || title || '|' || left(coalesce(description, ''), 500)) as job_hash,
  title, company, description, location, apply_url, source, first_seen
FROM jobs
ON CONFLICT (job_hash) DO NOTHING;

UPDATE jobs SET job_hash = md5(company || '|' || title || '|' || left(coalesce(description, ''), 500));
