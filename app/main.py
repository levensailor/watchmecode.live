"""FastAPI application factory."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.config import Settings, get_settings
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)

SESSION_COOKIE = "wmc_session"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_path = setup_logging(settings.log_dir)
    if not settings.session_secret_configured:
        logger.warning("SESSION_SECRET is not set; using an ephemeral secret for this process")

    app = FastAPI(title=settings.app_name, description=settings.app_tagline, version="0.1.0")
    app.state.settings = settings
    app.state.oauth = auth.build_oauth(settings)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=SESSION_COOKIE,
        same_site="lax",
        https_only=settings.is_vercel,
        max_age=60 * 60 * 24 * 7,
    )

    app.include_router(auth.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    logger.info("%s started; logging to %s", settings.app_name, log_path)
    return app
