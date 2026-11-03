-- watchmecode.live schema (Postgres / Supabase). SQLite equivalent: sql/schema.sql
-- Applied automatically on startup; safe to re-run.
-- Row level security is enabled with no policies so the public Supabase Data API
-- cannot read sessions; the app connects as the table owner and bypasses RLS.

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
    UNIQUE (session_id, path)
);

CREATE TABLE IF NOT EXISTS runs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    path       TEXT,
    data       TEXT,
    actor      TEXT NOT NULL,
    client_id  TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_session ON events (session_id, id);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
