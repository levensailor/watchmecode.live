-- watchmecode.live schema (SQLite). Postgres equivalent: sql/schema_postgres.sql
-- Applied automatically on startup; safe to re-run.

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    pin               TEXT NOT NULL CHECK (length(pin) = 6),
    candidate_name    TEXT NOT NULL,
    interviewer_email TEXT NOT NULL,
    language          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended'))
);

CREATE UNIQUE INDEX IF NOT EXISTS sessions_active_pin ON sessions (pin) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS sessions_interviewer ON sessions (interviewer_email, candidate_name);

CREATE TABLE IF NOT EXISTS files (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, path)
);

-- CodeMirror collab change sets (UTF-16 offsets) that took each file from version - 1 to version.
CREATE TABLE IF NOT EXISTS doc_updates (
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    client_id  TEXT NOT NULL,
    changes    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, path, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    entry_path  TEXT NOT NULL DEFAULT '',
    command     TEXT NOT NULL,
    stdout      TEXT NOT NULL DEFAULT '',
    stderr      TEXT NOT NULL DEFAULT '',
    exit_code   INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_session ON runs (session_id, id);

-- Append-only change feed polled by participants via GET /api/sessions/{id}/changes?since=
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    path       TEXT,
    data       TEXT,
    actor      TEXT NOT NULL,
    client_id  TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_session ON events (session_id, id);

-- Interview recordings: a window over the events feed plus the files at the moment recording started.
CREATE TABLE IF NOT EXISTS recordings (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    created_by    TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    start_cursor  INTEGER NOT NULL,
    end_cursor    INTEGER,
    initial_files TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS recordings_session ON recordings (session_id, started_at);

-- Multi-tenant organizations. Columns added to `sessions` (org_id, mode, title, candidate_email,
-- time_limit_minutes, started_at, submitted_at) are applied by the repository at startup.
CREATE TABLE IF NOT EXISTS organizations (
    id         TEXT PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_members (
    org_id     TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('admin', 'interviewer')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (org_id, email)
);

CREATE INDEX IF NOT EXISTS org_members_email ON org_members (email);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

-- Credentials and webhook secrets are encrypted (Fernet) before they reach this table.
CREATE TABLE IF NOT EXISTS integrations (
    org_id         TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider       TEXT NOT NULL CHECK (provider IN ('ashby', 'modernloop')),
    credentials    TEXT NOT NULL,
    webhook_secret TEXT,
    settings       TEXT NOT NULL DEFAULT '{}',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_event_at  TEXT,
    last_synced_at TEXT,
    last_error     TEXT,
    PRIMARY KEY (org_id, provider)
);

CREATE TABLE IF NOT EXISTS external_links (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    session_id     TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    provider       TEXT NOT NULL,
    external_type  TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    parent_id      TEXT,
    application_id TEXT,
    candidate_id   TEXT,
    data           TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (org_id, provider, external_type, external_id)
);

CREATE INDEX IF NOT EXISTS external_links_session ON external_links (session_id);
CREATE INDEX IF NOT EXISTS external_links_parent ON external_links (org_id, provider, parent_id);

CREATE TABLE IF NOT EXISTS assessment_templates (
    id                 TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    language           TEXT NOT NULL,
    files              TEXT NOT NULL,
    time_limit_minutes INTEGER,
    created_by         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS assessment_templates_org ON assessment_templates (org_id, name);

CREATE TABLE IF NOT EXISTS problem_cache (
    slug       TEXT NOT NULL,
    language   TEXT NOT NULL,
    name       TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    files      TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '{}',
    model      TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (slug, language)
);

CREATE INDEX IF NOT EXISTS problem_cache_creator ON problem_cache (created_by, created_at);
