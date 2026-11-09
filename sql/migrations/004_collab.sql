-- Collaborative editing (CodeMirror collab): a version per file and the change sets between versions.
-- The app applies this automatically at startup for every workspace schema (see sql/schema_postgres.sql);
-- this file documents the change for manual review or replay:
--   SET search_path TO watchmecode;  \i sql/migrations/004_collab.sql

ALTER TABLE files ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS doc_updates (
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    client_id  TEXT NOT NULL,
    changes    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, path, version)
);

ALTER TABLE doc_updates ENABLE ROW LEVEL SECURITY;
