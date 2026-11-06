"""Platform store: customer workspaces (tenants), memberships, usage metering, and Stripe event log."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

from app.repository import Repository, utcnow

logger = logging.getLogger(__name__)

TENANT_COLUMNS = (
    "id, slug, name, plan, status, schema_name, owner_email, allowed_domains, open_access, trial_ends_at, "
    "billing_interval, current_period_start, current_period_end, stripe_customer_id, stripe_subscription_id, "
    "deployment, custom_domain, schema_version, created_at, updated_at"
)
TENANT_UPDATABLE = {
    "name",
    "plan",
    "status",
    "allowed_domains",
    "open_access",
    "trial_ends_at",
    "billing_interval",
    "current_period_start",
    "current_period_end",
    "stripe_customer_id",
    "stripe_subscription_id",
    "deployment",
    "custom_domain",
    "schema_version",
}
ROLES = ("owner", "admin", "interviewer")


def _tenant(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["open_access"] = bool(row.get("open_access"))
    row["allowed_domains"] = [d for d in (row.get("allowed_domains") or "").split(",") if d]
    return row


class PlatformStore(Repository):
    SQLITE_SCRIPT = "platform.sql"
    POSTGRES_SCRIPT = "platform_postgres.sql"

    def _sqlite_upgrade(self, raw: sqlite3.Connection) -> None:
        return None

    # Tenants ----------------------------------------------------------------

    def create_tenant(
        self,
        *,
        slug: str,
        name: str,
        owner_email: str,
        schema_name: str,
        plan: str = "trial",
        status: str = "trialing",
        trial_ends_at: str | None = None,
        deployment: str = "shared",
        open_access: bool = False,
        schema_version: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        tenant_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.run(
                "INSERT INTO tenants (id, slug, name, plan, status, schema_name, owner_email, open_access, "
                "trial_ends_at, deployment, schema_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    slug,
                    name,
                    plan,
                    status,
                    schema_name,
                    owner_email,
                    int(open_access),
                    trial_ends_at,
                    deployment,
                    schema_version,
                    now,
                    now,
                ),
            )
            if owner_email:
                conn.run(
                    "INSERT INTO memberships (tenant_id, email, role, created_at) VALUES (?, ?, 'owner', ?)",
                    (tenant_id, owner_email.lower(), now),
                )
            row = conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE id = ?", (tenant_id,))
        logger.info("Tenant %s created (%s, plan %s)", slug, tenant_id, plan)
        return _tenant(row)  # type: ignore[return-value]

    def get_tenant(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return _tenant(conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE slug = ?", (slug,)))

    def get_tenant_by_id(self, tenant_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return _tenant(conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE id = ?", (tenant_id,)))

    def get_tenant_by_domain(self, domain: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return _tenant(conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE custom_domain = ?", (domain,)))

    def get_tenant_by_customer(self, customer_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return _tenant(
                conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE stripe_customer_id = ?", (customer_id,))
            )

    def list_tenants(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [_tenant(row) for row in conn.all(f"SELECT {TENANT_COLUMNS} FROM tenants ORDER BY created_at")]  # type: ignore[misc]

    def update_tenant(self, tenant_id: str, **fields: Any) -> dict[str, Any] | None:
        unknown = set(fields) - TENANT_UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable: {sorted(unknown)}")
        if "allowed_domains" in fields and isinstance(fields["allowed_domains"], list | tuple):
            fields["allowed_domains"] = ",".join(fields["allowed_domains"])
        if "open_access" in fields:
            fields["open_access"] = int(bool(fields["open_access"]))
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.connect() as conn:
            conn.run(
                f"UPDATE tenants SET {assignments}, updated_at = ? WHERE id = ?",
                (*fields.values(), utcnow(), tenant_id),
            )
            return _tenant(conn.one(f"SELECT {TENANT_COLUMNS} FROM tenants WHERE id = ?", (tenant_id,)))

    # Memberships ------------------------------------------------------------

    def get_membership(self, tenant_id: str, email: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.one(
                "SELECT tenant_id, email, role, created_at FROM memberships WHERE tenant_id = ? AND email = ?",
                (tenant_id, email.lower()),
            )

    def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                "SELECT email, role, created_at FROM memberships WHERE tenant_id = ? ORDER BY created_at",
                (tenant_id,),
            )

    def upsert_member(self, tenant_id: str, email: str, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}")
        with self.connect() as conn:
            conn.run(
                "INSERT INTO memberships (tenant_id, email, role, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, email) DO UPDATE SET role = excluded.role",
                (tenant_id, email.lower(), role, utcnow()),
            )
            return conn.one(
                "SELECT email, role, created_at FROM memberships WHERE tenant_id = ? AND email = ?",
                (tenant_id, email.lower()),
            )  # type: ignore[return-value]

    def remove_member(self, tenant_id: str, email: str) -> bool:
        with self.connect() as conn:
            return conn.run(
                "DELETE FROM memberships WHERE tenant_id = ? AND email = ? AND role <> 'owner'",
                (tenant_id, email.lower()),
            ) > 0

    def tenants_for_email(self, email: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {', '.join('t.' + c.strip() for c in TENANT_COLUMNS.split(','))}, m.role AS role "
                "FROM memberships m JOIN tenants t ON t.id = m.tenant_id WHERE m.email = ? ORDER BY t.created_at",
                (email.lower(),),
            )
        return [_tenant(row) for row in rows]  # type: ignore[misc]

    # Usage ------------------------------------------------------------------

    def record_usage(self, tenant_id: str, session_id: str, kind: str, credits: int) -> dict[str, Any] | None:
        """Idempotent per (tenant, session, kind); returns the new event or None if it was already counted."""
        event_id = uuid.uuid4().hex
        with self.connect() as conn:
            inserted = conn.run(
                "INSERT INTO usage_events (id, tenant_id, session_id, kind, credits, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (tenant_id, session_id, kind) DO NOTHING",
                (event_id, tenant_id, session_id, kind, credits, utcnow()),
            )
            if not inserted:
                return None
            return conn.one(
                "SELECT id, tenant_id, session_id, kind, credits, created_at, reported_at FROM usage_events WHERE id = ?",
                (event_id,),
            )

    def credits_used(self, tenant_id: str, since: str | None = None) -> int:
        with self.connect() as conn:
            row = conn.one(
                "SELECT COALESCE(SUM(credits), 0) AS used FROM usage_events WHERE tenant_id = ? AND created_at >= ?",
                (tenant_id, since or ""),
            )
        return int(row["used"]) if row else 0

    def count_usage(self, tenant_id: str, since: str, kind: str) -> int:
        with self.connect() as conn:
            row = conn.one(
                "SELECT COUNT(*) AS n FROM usage_events WHERE tenant_id = ? AND created_at >= ? AND kind = ?",
                (tenant_id, since, kind),
            )
        return int(row["n"]) if row else 0

    def session_metered(self, tenant_id: str, session_id: str) -> bool:
        with self.connect() as conn:
            return conn.one(
                "SELECT 1 AS hit FROM usage_events WHERE tenant_id = ? AND session_id = ? LIMIT 1",
                (tenant_id, session_id),
            ) is not None

    def recent_usage(self, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                "SELECT id, session_id, kind, credits, created_at, reported_at FROM usage_events "
                "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            )

    def mark_usage_reported(self, event_id: str) -> None:
        with self.connect() as conn:
            conn.run("UPDATE usage_events SET reported_at = ? WHERE id = ?", (utcnow(), event_id))

    # Stripe -----------------------------------------------------------------

    def record_stripe_event(self, event_id: str, event_type: str, tenant_id: str | None) -> bool:
        """True the first time an event id is seen (Stripe retries deliveries)."""
        with self.connect() as conn:
            return conn.run(
                "INSERT INTO stripe_events (id, type, tenant_id, received_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                (event_id, event_type, tenant_id, utcnow()),
            ) > 0
