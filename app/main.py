"""FastAPI application factory."""

from __future__ import annotations

import html
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, routes
from app.config import PROJECT_ROOT, Settings, get_settings
from app.logging_config import setup_logging
from app.repository import Repository
from app.runner import CodeRunner

logger = logging.getLogger(__name__)

SESSION_COOKIE = "wmc_session"
STATIC_DIR = PROJECT_ROOT / "static"


def asset_version() -> str:
    mtimes = sorted(int(path.stat().st_mtime) for path in STATIC_DIR.glob("*.*"))
    return str(mtimes[-1]) if mtimes else "0"


def render_index(settings: Settings) -> str:
    template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    replacements = {
        "{{APP_NAME}}": html.escape(settings.app_name),
        "{{APP_TAGLINE}}": html.escape(settings.app_tagline),
        "{{VERSION}}": asset_version(),
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

    app = FastAPI(title=settings.app_name, description=settings.app_tagline, version="0.1.0")
    app.state.settings = settings
    app.state.oauth = auth.build_oauth(settings)
    app.state.repo = repo
    app.state.runner = CodeRunner(settings)

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

    app.include_router(auth.router)
    app.include_router(routes.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "storage": repo.kind}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/favicon.ico", include_in_schema=False)
        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def index() -> HTMLResponse:
            return HTMLResponse(render_index(settings), headers={"Cache-Control": "no-cache"})

    logger.info("%s started (%s storage); logging to %s", settings.app_name, repo.kind, log_path)
    return app
