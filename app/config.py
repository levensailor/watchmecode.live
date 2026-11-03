"""Application settings loaded from the environment (and `.env` via python-dotenv)."""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


@dataclass(frozen=True)
class Settings:
    app_name: str = "watchmecode.live"
    app_tagline: str = "Live code-share IDE for technical interviews"
    app_author: str = "levensailor"
    public_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_scopes: str = "openid email profile"
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    session_secret_configured: bool = False
    database_url: str = ""
    sqlite_path: str = str(PROJECT_ROOT / "watchmecode.db")
    log_dir: str = str(PROJECT_ROOT / "logs")
    runs_dir: str = str(Path(tempfile.gettempdir()) / "watchmecode-runs")
    run_timeout_seconds: int = 20
    install_timeout_seconds: int = 120
    max_output_bytes: int = 64_000
    max_file_bytes: int = 500_000
    participant_token_max_age: int = 60 * 60 * 12
    node_version: str = "default"
    is_vercel: bool = False

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def uses_postgres(self) -> bool:
        return bool(self.database_url)


def load_settings() -> Settings:
    is_vercel = bool(_env("VERCEL"))
    tmp = Path(tempfile.gettempdir())
    defaults = Settings()
    session_secret = _env("SESSION_SECRET")
    return Settings(
        app_name=_env("APP_NAME", defaults.app_name),
        app_tagline=_env("APP_TAGLINE", defaults.app_tagline),
        app_author=_env("APP_AUTHOR", defaults.app_author),
        public_url=_env("PUBLIC_URL"),
        google_client_id=_env("GOOGLE_CLIENT_ID"),
        google_client_secret=_env("GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=_env("GOOGLE_REDIRECT_URI"),
        google_scopes=_env("GOOGLE_SCOPES", defaults.google_scopes),
        session_secret=session_secret or defaults.session_secret,
        session_secret_configured=bool(session_secret),
        database_url=_first_env("DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING"),
        sqlite_path=_env(
            "SQLITE_PATH",
            str(tmp / "watchmecode.db") if is_vercel else defaults.sqlite_path,
        ),
        log_dir=_env("LOG_DIR", defaults.log_dir),
        runs_dir=_env("RUNS_DIR", defaults.runs_dir),
        run_timeout_seconds=_env_int("RUN_TIMEOUT_SECONDS", defaults.run_timeout_seconds),
        install_timeout_seconds=_env_int("INSTALL_TIMEOUT_SECONDS", defaults.install_timeout_seconds),
        max_output_bytes=_env_int("MAX_OUTPUT_BYTES", defaults.max_output_bytes),
        max_file_bytes=_env_int("MAX_FILE_BYTES", defaults.max_file_bytes),
        participant_token_max_age=_env_int("PARTICIPANT_TOKEN_MAX_AGE", defaults.participant_token_max_age),
        node_version=_env("NODE_VERSION", defaults.node_version),
        is_vercel=is_vercel,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
