"""FastAPI application factory."""

from __future__ import annotations

import html
import logging
import time
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, orgs, platform_api, public_api, routes, site
from app.assets import content_version
from app.config import PROJECT_ROOT, Settings, get_settings
from app.crypto import Vault
from app.integrations import ashby, modernloop
from app.logging_config import setup_logging
from app.problems import routes as problem_routes
from app.problems.catalog import Catalog
from app.repository import Repository
from app.runner import CodeRunner
from app.sandbox_runner import SandboxRunner
from app.tenancy import RepoProxy, TenantMiddleware, TenantRegistry, current_site, tenant_url

logger = logging.getLogger(__name__)

SESSION_COOKIE = "wmc_session"
STATIC_DIR = PROJECT_ROOT / "static"


def asset_version() -> str:
    return content_version(*sorted(STATIC_DIR.glob("*.js")), *sorted(STATIC_DIR.glob("*.css")), *sorted(STATIC_DIR.glob("*.html")))


def vendor_version() -> str:
    return content_version(STATIC_DIR / "vendor" / "codemirror.js")


def render_page(settings: Settings, filename: str = "index.html") -> str:
    template = (STATIC_DIR / filename).read_text(encoding="utf-8")
    replacements = {
        "{{APP_NAME}}": html.escape(settings.app_name),
        "{{APP_TAGLINE}}": html.escape(settings.app_tagline),
        "{{APP_URL}}": html.escape(settings.public_url or "/"),
        "{{CONTACT_URL}}": html.escape(settings.contact_url),
        "{{LEGAL_UPDATED}}": html.escape(settings.legal_updated),
        "{{VERSION}}": asset_version(),
        "{{VENDOR_VERSION}}": vendor_version(),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_path = setup_logging(settings.log_dir)
    if not settings.session_secret_configured:
        logger.warning("SESSION_SECRET is not set; using an ephemeral secret for this process")

    repo = Repository(settings)
    repo.init_schema()
    registry = TenantRegistry(settings, repo)
    registry.init()

    app = FastAPI(title=settings.app_name, description=settings.app_tagline, version="0.1.0")
    app.state.settings = settings
    app.state.oauth = auth.build_oauth(settings)
    app.state.repo = RepoProxy(repo)
    app.state.tenants = registry
    local_runner = CodeRunner(settings)
    app.state.runner = SandboxRunner(settings, local_runner) if settings.sandbox_backend == "sandbox" else local_runner
    app.state.vault = Vault(settings)
    app.state.http_transport = None
    app.state.problem_catalog = Catalog(settings)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=SESSION_COOKIE,
        same_site="lax",
        https_only=settings.is_vercel,
        max_age=60 * 60 * 24 * 7,
    )
    app.add_middleware(TenantMiddleware, registry=registry)

    app.include_router(auth.router)
    app.include_router(routes.router)
    app.include_router(orgs.router)
    app.include_router(public_api.router)
    app.include_router(ashby.router)
    app.include_router(ashby.partner_router)
    app.include_router(modernloop.router)
    app.include_router(platform_api.router)
    app.include_router(problem_routes.router)
    app.include_router(site.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "storage": repo.kind, "mode": settings.tenant_mode if registry.enabled else "single"}

    @app.get("/j/{token}", include_in_schema=False)
    def join_link(token: str) -> RedirectResponse:
        target = f"/#/j/{quote(token, safe='')}"
        if registry.enabled and current_site.get() == "apex":
            target = tenant_url(settings, registry.default_tenant() or {"slug": settings.tenant_slug}) + target
        return RedirectResponse(target, status_code=302)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/favicon.ico", include_in_schema=False)
        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def index(request: Request) -> HTMLResponse:
            if registry.enabled and current_site.get() == "apex":
                return site.render(request, "home")
            return HTMLResponse(render_page(settings), headers={"Cache-Control": "no-cache"})

        @app.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
        def privacy() -> HTMLResponse:
            return HTMLResponse(render_page(settings, "privacy.html"))

        @app.get("/terms", include_in_schema=False, response_class=HTMLResponse)
        def terms() -> HTMLResponse:
            return HTMLResponse(render_page(settings, "terms.html"))

    logger.info("%s started (%s storage); logging to %s", settings.app_name, repo.kind, log_path)
    return app
