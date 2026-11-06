-- Additive migration for organizations, API keys, integrations, and take-home sessions.
-- The app applies this automatically at startup (see sql/schema_postgres.sql); this file
-- documents the change for manual review or replay against Postgres:
--   SET search_path TO watchmecode;  \i sql/migrations/002_organizations_and_integrations.sql

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS org_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS candidate_email TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS time_limit_minutes INTEGER;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS started_at TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS submitted_at TEXT;
CREATE INDEX IF NOT EXISTS sessions_org ON sessions (org_id, created_at);

-- New tables: see the "organizations" section of sql/schema_postgres.sql
-- (organizations, org_members, api_keys, integrations, external_links, assessment_templates).
