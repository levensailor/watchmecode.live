"""Host-based tenant routing.

`watchmecode.live` (the apex) serves the product site and the auth hub; `{slug}.watchmecode.live`
serves that customer's workspace from its own Postgres schema (or SQLite file locally).
With ROOT_DOMAIN unset the app runs as a single workspace, exactly as before.
"""

from __future__ import annotations

import base64
import html
import logging
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse

from app.config import Settings
from app.crypto import key_workspace
from app.platform import PlatformStore
from app.repository import Repository

logger = logging.getLogger(__name__)

TENANT_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
CACHE_SECONDS = 30.0

SiteKind = Literal["apex", "tenant", "unknown"]

current_tenant: ContextVar[dict[str, Any] | None] = ContextVar("current_tenant", default=None)
current_repo: ContextVar[Repository | None] = ContextVar("current_repo", default=None)
current_site: ContextVar[SiteKind] = ContextVar("current_site", default="tenant")


@dataclass(frozen=True)
class HostMatch:
    kind: SiteKind
    tenant: dict[str, Any] | None = None
    host: str = ""


def schema_for(slug: str) -> str:
    return "t_" + slug.replace("-", "_")


def slug_error(settings: Settings, slug: str) -> str | None:
    if not TENANT_SLUG.match(slug) or slug.endswith("-") or "--" in slug:
        return "Use 2–31 lowercase letters, numbers, or single hyphens, starting with a letter."
    if slug in settings.reserved or slug == settings.tenant_slug:
        return "That subdomain is reserved."
    return None


def strip_port(host: str) -> str:
    return host.split(":", 1)[0].strip().lower().rstrip(".")


def _scheme_and_port(settings: Settings) -> tuple[str, str]:
    if not settings.public_url:
        return "https", ""
    parts = urlsplit(settings.public_url)
    return parts.scheme or "https", f":{parts.port}" if parts.port else ""


def apex_url(settings: Settings) -> str:
    if settings.public_url:
        return settings.public_url.rstrip("/")
    scheme, port = _scheme_and_port(settings)
    return f"{scheme}://{settings.root_domain}{port}"


def tenant_url(settings: Settings, tenant: dict[str, Any]) -> str:
    scheme, port = _scheme_and_port(settings)
    if tenant.get("custom_domain"):
        return f"https://{tenant['custom_domain']}"
    return f"{scheme}://{tenant['slug']}.{settings.root_domain}{port}"


def trial_end(settings: Settings) -> str:
    return (datetime.now(UTC) + timedelta(days=settings.trial_days)).isoformat(timespec="seconds")


class RepoProxy:
    """Stands in for `app.state.repo`; resolves to the current request's tenant repository."""

    def __init__(self, default: Repository) -> None:
        self._default = default

    @property
    def target(self) -> Repository:
        return current_repo.get() or self._default

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)


class TenantRegistry:
    def __init__(self, settings: Settings, default_repo: Repository) -> None:
        self.settings = settings
        self.default_repo = default_repo
        self.platform: PlatformStore | None = None
        if settings.multi_tenant:
            base = Path(settings.sqlite_path)
            self.platform = PlatformStore(
                replace(
                    settings,
                    db_schema=settings.platform_schema,
                    sqlite_path=settings.platform_sqlite_path or str(base.with_name("platform.db")),
                )
            )
        self._tenant_dir = Path(settings.tenant_sqlite_dir or Path(settings.sqlite_path).parent / "tenants")
        self._repos: dict[str, Repository] = {}
        self._tenants: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._roles: dict[tuple[str, str], tuple[float, str | None]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.platform is not None

    def init(self) -> None:
        if not self.platform:
            return
        self.platform.init_schema()
        if not self.platform.get_tenant(self.settings.tenant_slug):
            self.platform.create_tenant(
                slug=self.settings.tenant_slug,
                name=self.settings.app_name,
                owner_email="",
                schema_name=self.settings.db_schema,
                plan="enterprise",
                status="active",
                open_access=True,
                schema_version=Repository.schema_version(),
            )

    # Lookup -----------------------------------------------------------------

    def invalidate(self, slug: str | None = None) -> None:
        with self._lock:
            if slug is None:
                self._tenants.clear()
                self._roles.clear()
            else:
                self._tenants.pop(slug, None)
                self._roles = {k: v for k, v in self._roles.items() if k[0] != slug}

    def tenant(self, slug: str) -> dict[str, Any] | None:
        if not self.platform:
            return None
        cached = self._tenants.get(slug)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        tenant = self.platform.get_tenant(slug)
        with self._lock:
            self._tenants[slug] = (time.monotonic() + CACHE_SECONDS, tenant)
        return tenant

    def default_tenant(self) -> dict[str, Any] | None:
        return self.tenant(self.settings.tenant_slug)

    def match(self, raw_host: str) -> HostMatch:
        if not self.platform:
            return HostMatch("tenant")
        host = strip_port(raw_host)
        root = self.settings.root_domain
        if host in (root, f"www.{root}"):
            return HostMatch("apex", self.default_tenant(), host)
        if host.endswith(f".{root}"):
            label = host[: -len(root) - 1]
            tenant = self.tenant(label) if "." not in label else None
            if tenant and tenant["status"] != "deleted":
                return HostMatch("tenant", tenant, host)
            return HostMatch("unknown", None, host)
        tenant = self.platform.get_tenant_by_domain(host)
        if tenant:
            return HostMatch("tenant", tenant, host)
        # Preview deployments and other hosts get the product site.
        return HostMatch("apex", self.default_tenant(), host)

    # Repositories -----------------------------------------------------------

    def repo_for(self, tenant: dict[str, Any] | None) -> Repository:
        if tenant is None or (
            tenant["slug"] == self.settings.tenant_slug and tenant["schema_name"] == self.settings.db_schema
        ):
            return self.default_repo
        slug = tenant["slug"]
        repo = self._repos.get(slug)
        if repo is None:
            repo = Repository(
                replace(
                    self.settings,
                    db_schema=tenant["schema_name"],
                    sqlite_path=str(self._tenant_dir / f"{slug}.db"),
                )
            )
            self._migrate(tenant, repo)
            with self._lock:
                self._repos[slug] = repo
        return repo

    def _migrate(self, tenant: dict[str, Any], repo: Repository, force: bool = False) -> bool:
        version = Repository.schema_version()
        local_missing = not repo.postgres and not Path(repo._sqlite_path).exists()
        if not force and tenant.get("schema_version") == version and not local_missing:
            return False
        repo.init_schema()
        if self.platform:
            self.platform.update_tenant(tenant["id"], schema_version=version)
        logger.info("Tenant %s schema migrated to %s", tenant["slug"], version)
        return True

    def migrate_all(self, force: bool = False) -> list[dict[str, Any]]:
        results = []
        for tenant in self.platform.list_tenants() if self.platform else []:
            repo = self.repo_for(tenant) if tenant["slug"] != self.settings.tenant_slug else self.default_repo
            changed = self._migrate(tenant, repo, force=force)
            results.append({"slug": tenant["slug"], "schema": tenant["schema_name"], "migrated": changed})
        return results

    def provision(self, *, slug: str, name: str, owner_email: str, plan: str = "trial") -> dict[str, Any]:
        assert self.platform is not None
        tenant = self.platform.create_tenant(
            slug=slug,
            name=name,
            owner_email=owner_email,
            schema_name=schema_for(slug),
            plan=plan,
            status="trialing" if plan == "trial" else "active",
            trial_ends_at=trial_end(self.settings) if plan == "trial" else None,
        )
        self.repo_for(tenant)
        self.invalidate(slug)
        return self.tenant(slug) or tenant

    @contextmanager
    def use(self, tenant: dict[str, Any] | None) -> Iterator[Repository]:
        repo = self.repo_for(tenant)
        tokens = (current_tenant.set(tenant), current_repo.set(repo))
        try:
            yield repo
        finally:
            current_repo.reset(tokens[1])
            current_tenant.reset(tokens[0])

    def active_tenants(self) -> list[dict[str, Any]]:
        if not self.platform:
            return [None]  # type: ignore[list-item]
        return [t for t in self.platform.list_tenants() if t["status"] not in ("deleted", "canceled")]

    # Access -----------------------------------------------------------------

    def role_for(self, tenant: dict[str, Any] | None, email: str) -> str | None:
        """Owner/admin/interviewer for members; interviewer for allowed domains or open workspaces."""
        if tenant is None or not self.platform:
            return "interviewer"
        key = (tenant["slug"], email.lower())
        cached = self._roles.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        membership = self.platform.get_membership(tenant["id"], email)
        role = membership["role"] if membership else None
        domain = email.lower().rsplit("@", 1)[-1]
        if role is None and (tenant["open_access"] or domain in tenant["allowed_domains"]):
            role = "interviewer"
        with self._lock:
            self._roles[key] = (time.monotonic() + CACHE_SECONDS, role)
        return role


def api_key_workspace(authorization: str) -> str | None:
    """Workspace slug from a `wmc_<slug>.<token>` key sent as Bearer or as the Basic username."""
    scheme, _, value = authorization.partition(" ")
    value = value.strip()
    if scheme.lower() == "basic":
        try:
            value = base64.b64decode(value).decode().partition(":")[0]
        except (ValueError, UnicodeDecodeError):
            return None
    elif scheme.lower() != "bearer":
        return None
    return key_workspace(value)


def _not_found(settings: Settings, host: str) -> HTMLResponse:
    apex = html.escape(apex_url(settings))
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>No workspace here · {html.escape(settings.app_name)}</title>"
        "<link rel=icon href='/static/favicon.svg'><link rel=stylesheet href='/static/site.css'>"
        "<main class='site-narrow'><section class='tile'><h1>No workspace here</h1>"
        f"<p>Nothing is set up at <code>{html.escape(host)}</code> yet.</p>"
        f"<p><a class='btn btn-primary' href='{apex}/signup'>Claim this subdomain</a> "
        f"<a class='btn' href='{apex}/'>Go to {html.escape(settings.app_name)}</a></p></section></main>"
    )
    return HTMLResponse(body, status_code=404)


class TenantMiddleware:
    """Pure ASGI so the tenant context vars reach endpoints, threadpool handlers, and background tasks."""

    def __init__(self, app, registry: TenantRegistry) -> None:
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.registry.enabled:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        match = await run_in_threadpool(self.registry.match, headers.get("host", ""))
        if match.kind == "apex":
            scoped = api_key_workspace(headers.get("authorization", ""))
            tenant = await run_in_threadpool(self.registry.tenant, scoped) if scoped else None
            if tenant is not None and tenant["status"] != "deleted":
                match = HostMatch("tenant", tenant, match.host)
        if match.kind == "unknown":
            path = scope.get("path", "")
            if path.startswith("/static/") or path == "/favicon.svg":
                await self.app(scope, receive, send)
            else:
                await _not_found(self.registry.settings, match.host)(scope, receive, send)
            return
        repo = await run_in_threadpool(self.registry.repo_for, match.tenant)
        scope.setdefault("state", {})
        scope["state"]["tenant"] = match.tenant
        scope["state"]["site"] = match.kind
        tokens = (current_tenant.set(match.tenant), current_repo.set(repo), current_site.set(match.kind))
        try:
            await self.app(scope, receive, send)
        finally:
            current_site.reset(tokens[2])
            current_repo.reset(tokens[1])
            current_tenant.reset(tokens[0])
