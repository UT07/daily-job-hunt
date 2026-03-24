-- ============================================================
--  Migration 002: GDPR compliance
--  Version: 002
--  Created: 2026-03-24
--  Description: Adds audit_log table for tracking all authenticated
--               API actions. GDPR columns (gdpr_consent_at,
--               gdpr_deletion_requested_at) already exist on users
--               from migration 001.
-- ============================================================

-- Audit log for all authenticated API actions
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action TEXT NOT NULL,          -- e.g., "profile.update", "job.status_change", "gdpr.export"
    resource_type TEXT,            -- e.g., "user", "job", "resume", "run"
    resource_id TEXT,              -- ID of the affected resource
    details JSONB,                 -- Additional context
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_log_select ON audit_log
    FOR SELECT USING (user_id = auth.uid());
CREATE POLICY audit_log_insert ON audit_log
    FOR INSERT WITH CHECK (user_id = auth.uid());
-- No update/delete — audit logs are immutable
