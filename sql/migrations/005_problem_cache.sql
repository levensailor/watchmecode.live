-- Company problem library: AI-written problems cached per source problem and language, so each one is
-- generated once per workspace. Rows also back the per-interviewer hourly generation limit.
-- The app applies this automatically at startup for every workspace schema (see sql/schema_postgres.sql);
-- this file documents the change for manual review or replay:
--   SET search_path TO watchmecode;  \i sql/migrations/005_problem_cache.sql

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

ALTER TABLE problem_cache ENABLE ROW LEVEL SECURITY;
