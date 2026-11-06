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
    contact_url: str = "https://github.com/levensailor/watchmecode.live/issues"
    legal_updated: str = "November 6, 2026"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_scopes: str = "openid email profile"
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    session_secret_configured: bool = False
    database_url: str = ""
    db_schema: str = "watchmecode"
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
    integration_encryption_key: str = ""
    join_link_max_age: int = 60 * 60 * 24 * 30
    ashby_api_base: str = "https://api.ashbyhq.com"
    ashby_partner_id: str = "watchmecode"
    modernloop_api_base: str = "https://api.modernloop.io"
    resend_api_key: str = ""
    email_from: str = ""
    cron_secret: str = ""
    http_timeout_seconds: float = 15.0
    root_domain: str = ""
    tenant_mode: str = "single"
    tenant_slug: str = "app"
    platform_schema: str = "platform"
    platform_sqlite_path: str = ""
    tenant_sqlite_dir: str = ""
    reserved_subdomains: str = "www,app,api,admin,auth,docs,status,mail,billing,help,blog,support,static"
    auth_hub_url: str = ""
    handoff_private_key: str = ""
    handoff_public_key: str = ""
    handoff_max_age: int = 120
    trial_days: int = 14
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_api_base: str = "https://api.stripe.com"
    stripe_prices: str = ""
    stripe_meter_event: str = "interview_credits"
    sandbox_backend: str = "local"
    sandbox_session_minutes: int = 60
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    ai_gateway_model: str = "anthropic/claude-sonnet-5.5"
    ai_gateway_fallback_model: str = "google/gemini-2.5-flash"
    ai_gateway_api_key: str = ""
    ai_review_timeout_seconds: float = 50.0
    vercel_token: str = ""
    vercel_team_id: str = ""
    vercel_project_id: str = ""

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def multi_tenant(self) -> bool:
        return self.tenant_mode == "multi" and bool(self.root_domain)

    @property
    def reserved(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.reserved_subdomains.split(",") if part.strip())

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key)

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
        contact_url=_env("CONTACT_URL", defaults.contact_url),
        legal_updated=_env("LEGAL_UPDATED", defaults.legal_updated),
        google_client_id=_env("GOOGLE_CLIENT_ID"),
        google_client_secret=_env("GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=_env("GOOGLE_REDIRECT_URI"),
        google_scopes=_env("GOOGLE_SCOPES", defaults.google_scopes),
        session_secret=session_secret or defaults.session_secret,
        session_secret_configured=bool(session_secret),
        database_url=_first_env("DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING"),
        db_schema=_env("DB_SCHEMA", defaults.db_schema),
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
        integration_encryption_key=_env("INTEGRATION_ENCRYPTION_KEY"),
        join_link_max_age=_env_int("JOIN_LINK_MAX_AGE", defaults.join_link_max_age),
        ashby_api_base=_env("ASHBY_API_BASE", defaults.ashby_api_base),
        ashby_partner_id=_env("ASHBY_PARTNER_ID", defaults.ashby_partner_id),
        modernloop_api_base=_env("MODERNLOOP_API_BASE", defaults.modernloop_api_base),
        resend_api_key=_env("RESEND_API_KEY"),
        email_from=_env("EMAIL_FROM"),
        cron_secret=_env("CRON_SECRET"),
        root_domain=_env("ROOT_DOMAIN").lower(),
        tenant_mode=_env("TENANT_MODE", "multi" if _env("ROOT_DOMAIN") else defaults.tenant_mode).lower(),
        tenant_slug=_env("TENANT_SLUG", defaults.tenant_slug).lower(),
        platform_schema=_env("PLATFORM_SCHEMA", defaults.platform_schema),
        platform_sqlite_path=_env("PLATFORM_SQLITE_PATH", str(tmp / "platform.db") if is_vercel else ""),
        tenant_sqlite_dir=_env("TENANT_SQLITE_DIR", str(tmp / "tenants") if is_vercel else ""),
        reserved_subdomains=_env("RESERVED_SUBDOMAINS", defaults.reserved_subdomains),
        auth_hub_url=_env("AUTH_HUB_URL"),
        handoff_private_key=_env("HANDOFF_PRIVATE_KEY"),
        handoff_public_key=_env("HANDOFF_PUBLIC_KEY"),
        handoff_max_age=_env_int("HANDOFF_MAX_AGE", defaults.handoff_max_age),
        trial_days=_env_int("TRIAL_DAYS", defaults.trial_days),
        stripe_secret_key=_env("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=_env("STRIPE_WEBHOOK_SECRET"),
        stripe_api_base=_env("STRIPE_API_BASE", defaults.stripe_api_base),
        stripe_prices=_env("STRIPE_PRICES"),
        stripe_meter_event=_env("STRIPE_METER_EVENT", defaults.stripe_meter_event),
        sandbox_backend=_env("SANDBOX_BACKEND", defaults.sandbox_backend).lower(),
        sandbox_session_minutes=_env_int("SANDBOX_SESSION_MINUTES", defaults.sandbox_session_minutes),
        ai_gateway_base_url=_env("AI_GATEWAY_BASE_URL", defaults.ai_gateway_base_url),
        ai_gateway_model=_env("AI_GATEWAY_MODEL", defaults.ai_gateway_model),
        ai_gateway_fallback_model=_env("AI_GATEWAY_FALLBACK_MODEL", defaults.ai_gateway_fallback_model),
        ai_gateway_api_key=_env("AI_GATEWAY_API_KEY"),
        ai_review_timeout_seconds=float(_env("AI_REVIEW_TIMEOUT_SECONDS") or defaults.ai_review_timeout_seconds),
        vercel_token=_env("VERCEL_TOKEN"),
        vercel_team_id=_env("VERCEL_TEAM_ID"),
        vercel_project_id=_env("VERCEL_PROJECT_ID"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
