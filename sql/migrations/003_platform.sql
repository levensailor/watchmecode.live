-- Platform schema for multi-tenant workspaces (tenants, memberships, usage, Stripe events).
-- The app applies sql/platform_postgres.sql automatically at startup when ROOT_DOMAIN is set;
-- this file documents the change for manual review or replay against Postgres:
--   \i sql/migrations/003_platform.sql
-- Existing data stays in the "watchmecode" schema and becomes the default "app" workspace.
-- New workspaces get their own schema named t_<slug> (hyphens become underscores).

CREATE SCHEMA IF NOT EXISTS platform;
SET search_path TO platform;

\i sql/platform_postgres.sql
