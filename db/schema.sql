-- ============================================================
--  Supabase Schema for Job Automation SaaS
--  Multi-tenant schema with Row Level Security (RLS)
--
--  Tables: users, user_resumes, user_search_configs, jobs, runs
--  All tenant data is isolated via RLS on user_id = auth.uid()
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Trigger function: auto-update updated_at ──────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
--  users: replaces config.yaml profile section
-- ============================================================

CREATE TABLE users (
    id                        UUID PRIMARY KEY DEFAULT auth.uid(),
    email                     TEXT UNIQUE NOT NULL,
    name                      TEXT,
    phone                     TEXT,
    location                  TEXT,
    visa_status               TEXT,
    github                    TEXT,
    linkedin                  TEXT,
    website                   TEXT,
    work_authorizations       JSONB,
    candidate_context         TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    gdpr_consent_at           TIMESTAMPTZ,
    gdpr_deletion_requested_at TIMESTAMPTZ
);

CREATE TRIGGER users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_select ON users
    FOR SELECT USING (id = auth.uid());

CREATE POLICY users_insert ON users
    FOR INSERT WITH CHECK (id = auth.uid());

CREATE POLICY users_update ON users
    FOR UPDATE USING (id = auth.uid());


-- ============================================================
--  user_resumes: replaces config.yaml resumes section
-- ============================================================

CREATE TABLE user_resumes (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resume_key               TEXT NOT NULL,
    label                    TEXT,
    tex_content              TEXT,
    google_doc_template_id   TEXT,
    target_roles             TEXT[],
    template_style           TEXT DEFAULT 'professional',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, resume_key)
);

CREATE INDEX idx_user_resumes_user_id ON user_resumes(user_id);

CREATE TRIGGER user_resumes_updated_at
    BEFORE UPDATE ON user_resumes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE user_resumes ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_resumes_select ON user_resumes
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY user_resumes_insert ON user_resumes
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY user_resumes_update ON user_resumes
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY user_resumes_delete ON user_resumes
    FOR DELETE USING (user_id = auth.uid());


-- ============================================================
--  user_search_configs: replaces config.yaml search section
-- ============================================================

CREATE TABLE user_search_configs (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    queries                  TEXT[],
    locations                JSONB,
    geo_regions              JSONB,
    experience_levels        TEXT[],
    days_back                INT DEFAULT 7,
    max_jobs_per_run         INT DEFAULT 15,
    min_match_score          INT DEFAULT 60,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id)
);

CREATE INDEX idx_user_search_configs_user_id ON user_search_configs(user_id);

CREATE TRIGGER user_search_configs_updated_at
    BEFORE UPDATE ON user_search_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

ALTER TABLE user_search_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_search_configs_select ON user_search_configs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY user_search_configs_insert ON user_search_configs
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY user_search_configs_update ON user_search_configs
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY user_search_configs_delete ON user_search_configs
    FOR DELETE USING (user_id = auth.uid());


-- ============================================================
--  jobs: migrated from SQLite job_db.py, now multi-tenant
-- ============================================================

CREATE TABLE jobs (
    job_id                   TEXT NOT NULL,
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title                    TEXT NOT NULL,
    company                  TEXT NOT NULL,
    location                 TEXT,
    description              TEXT,
    apply_url                TEXT,
    source                   TEXT,
    match_score              REAL DEFAULT 0,
    ats_score                REAL DEFAULT 0,
    hiring_manager_score     REAL DEFAULT 0,
    tech_recruiter_score     REAL DEFAULT 0,
    matched_resume           TEXT,
    tailored_pdf_path        TEXT,
    cover_letter_pdf_path    TEXT,
    resume_doc_url           TEXT,
    resume_s3_url            TEXT,
    cover_letter_s3_url      TEXT,
    linkedin_contacts        TEXT,
    application_status       TEXT DEFAULT 'New',
    first_seen               TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, user_id)
);

CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_company ON jobs(user_id, company);
CREATE INDEX idx_jobs_first_seen ON jobs(user_id, first_seen);
CREATE INDEX idx_jobs_match_score ON jobs(user_id, match_score);

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY jobs_select ON jobs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY jobs_insert ON jobs
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY jobs_update ON jobs
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY jobs_delete ON jobs
    FOR DELETE USING (user_id = auth.uid());


-- ============================================================
--  runs: pipeline run history, now multi-tenant
-- ============================================================

CREATE TABLE runs (
    run_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_date                 DATE NOT NULL,
    run_time                 TIME NOT NULL,
    raw_jobs                 INT DEFAULT 0,
    unique_jobs              INT DEFAULT 0,
    matched_jobs             INT DEFAULT 0,
    resumes_generated        INT DEFAULT 0,
    status                   TEXT DEFAULT 'running',
    completed_at             TIMESTAMPTZ,
    CONSTRAINT valid_status CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX idx_runs_user_id ON runs(user_id);
CREATE INDEX idx_runs_date ON runs(user_id, run_date);

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY runs_select ON runs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY runs_insert ON runs
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY runs_update ON runs
    FOR UPDATE USING (user_id = auth.uid());
