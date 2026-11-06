-- watchmecode.live platform schema (SQLite). Postgres equivalent: sql/platform_postgres.sql
-- One row per customer workspace; each workspace's data lives in its own schema or SQLite file.

CREATE TABLE IF NOT EXISTS tenants (
    id                     TEXT PRIMARY KEY,
    slug                   TEXT NOT NULL UNIQUE,
    name                   TEXT NOT NULL,
    plan                   TEXT NOT NULL DEFAULT 'trial',
    status                 TEXT NOT NULL DEFAULT 'trialing',
    schema_name            TEXT NOT NULL,
    owner_email            TEXT NOT NULL,
    allowed_domains        TEXT NOT NULL DEFAULT '',
    open_access            INTEGER NOT NULL DEFAULT 0,
    trial_ends_at          TEXT,
    billing_interval       TEXT,
    current_period_start   TEXT,
    current_period_end     TEXT,
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    deployment             TEXT NOT NULL DEFAULT 'shared',
    custom_domain          TEXT,
    schema_version         TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS tenants_custom_domain ON tenants (custom_domain) WHERE custom_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS tenants_stripe_customer ON tenants (stripe_customer_id);

CREATE TABLE IF NOT EXISTS memberships (
    tenant_id  TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'interviewer',
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS memberships_email ON memberships (email);

CREATE TABLE IF NOT EXISTS usage_events (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    credits     INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    reported_at TEXT,
    UNIQUE (tenant_id, session_id, kind)
);

CREATE INDEX IF NOT EXISTS usage_events_tenant ON usage_events (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS stripe_events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    tenant_id   TEXT,
    received_at TEXT NOT NULL
);
