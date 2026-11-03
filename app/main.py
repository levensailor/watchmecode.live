"""FastAPI application factory."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request

from app.config import Settings, get_settings
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    log_path = setup_logging(settings.log_dir)

    app = FastAPI(title=settings.app_name, description=settings.app_tagline, version="0.1.0")
    app.state.settings = settings

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
        return response

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    logger.info("%s started; logging to %s", settings.app_name, log_path)
    return app
