-- Quality Pipeline: Phase 2.7 + 2.9 schema changes

-- 0. Check if match_score column exists (backward compat)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'jobs' AND column_name = 'match_score') THEN
        ALTER TABLE jobs ADD COLUMN match_score FLOAT;
    END IF;
END $$;

-- 1. Add canonical_hash and new score columns to jobs table
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS canonical_hash TEXT,
  ADD COLUMN IF NOT EXISTS base_ats_score INTEGER,
  ADD COLUMN IF NOT EXISTS base_hm_score INTEGER,
  ADD COLUMN IF NOT EXISTS base_tr_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_ats_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_hm_score INTEGER,
  ADD COLUMN IF NOT EXISTS tailored_tr_score INTEGER,
  ADD COLUMN IF NOT EXISTS final_score FLOAT,
  ADD COLUMN IF NOT EXISTS score_version INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS score_status TEXT DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS writing_quality_score FLOAT;

-- 2. Add canonical_hash to jobs_raw
ALTER TABLE jobs_raw
  ADD COLUMN IF NOT EXISTS canonical_hash TEXT;

-- 3. Create indexes on canonical_hash for cross-run dedup
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_hash ON jobs(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_raw_canonical_hash ON jobs_raw(canonical_hash);

-- 4. Create seen_jobs table (replaces seen_jobs.json)
CREATE TABLE IF NOT EXISTS seen_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id TEXT,
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  canonical_hash TEXT NOT NULL,
  first_seen DATE NOT NULL,
  last_seen DATE NOT NULL,
  title TEXT,
  company TEXT,
  score FLOAT DEFAULT 0,
  matched BOOLEAN DEFAULT false,
  UNIQUE(user_id, canonical_hash)
);

-- 5. Create pipeline_adjustments table (self-improvement)
CREATE TABLE IF NOT EXISTS pipeline_adjustments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  adjustment_type TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  payload JSONB NOT NULL,
  previous_state JSONB,
  reason TEXT NOT NULL,
  evidence JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  applied_at TIMESTAMPTZ,
  reverted_at TIMESTAMPTZ,
  reviewed_by UUID,
  run_id UUID,
  cooldown_until TIMESTAMPTZ
);

-- 6. Create prompt_versions table
CREATE TABLE IF NOT EXISTS prompt_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  prompt_name TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  active_from TIMESTAMPTZ DEFAULT now(),
  active_to TIMESTAMPTZ,
  metrics JSONB,
  created_by TEXT DEFAULT 'manual'
);

-- 7. Create pipeline_runs table
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  jobs_scraped INTEGER DEFAULT 0,
  jobs_new INTEGER DEFAULT 0,
  jobs_scored INTEGER DEFAULT 0,
  jobs_matched INTEGER DEFAULT 0,
  jobs_tailored INTEGER DEFAULT 0,
  avg_base_score FLOAT,
  avg_final_score FLOAT,
  avg_writing_quality FLOAT,
  active_adjustments JSONB,
  scraper_stats JSONB,
  model_stats JSONB,
  status TEXT DEFAULT 'running'
);

-- 8. RLS policies
ALTER TABLE seen_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE prompt_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY seen_jobs_user_policy ON seen_jobs
  FOR ALL USING (user_id = auth.uid());

CREATE POLICY pipeline_adj_user_policy ON pipeline_adjustments
  FOR ALL USING (user_id = auth.uid());

CREATE POLICY prompt_ver_user_policy ON prompt_versions
  FOR ALL USING (user_id = auth.uid());

CREATE POLICY pipeline_runs_user_select ON pipeline_runs
  FOR SELECT USING (user_id = auth.uid());
