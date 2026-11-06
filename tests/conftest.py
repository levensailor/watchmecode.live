from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.config import load_settings
from app.main import SESSION_COOKIE, create_app

INTERVIEWER = {"name": "Test Interviewer", "email": "interviewer@example.com", "picture": None}
OTHER_INTERVIEWER = {"name": "Other Interviewer", "email": "other@example.com", "picture": None}


@pytest.fixture
def settings(tmp_path):
    return replace(
        load_settings(),
        database_url="",
        sqlite_path=str(tmp_path / "test.db"),
        log_dir=str(tmp_path / "logs"),
        runs_dir=str(tmp_path / "runs"),
        session_secret="test-secret",
        session_secret_configured=True,
        google_client_id="test-client",
        google_client_secret="test-secret",
        google_redirect_uri="http://testserver/auth/google/callback",
        run_timeout_seconds=30,
        public_url="",
        root_domain="",
        tenant_mode="single",
        stripe_secret_key="",
        stripe_webhook_secret="",
        handoff_private_key="",
        handoff_public_key="",
        auth_hub_url="",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


def session_cookie(secret: str, user: dict, **extra) -> str:
    payload = base64.b64encode(json.dumps({"user": user, **extra}).encode())
    return TimestampSigner(secret).sign(payload).decode()


ROOT = "watchmecode.test"


@pytest.fixture
def multi_settings(settings, tmp_path):
    return replace(
        settings,
        public_url=f"https://{ROOT}",
        root_domain=ROOT,
        tenant_mode="multi",
        platform_sqlite_path=str(tmp_path / "platform.db"),
        tenant_sqlite_dir=str(tmp_path / "tenants"),
        google_redirect_uri="",
    )


@pytest.fixture
def multi_app(multi_settings):
    return create_app(multi_settings)


def tenant_client(app, settings, slug: str | None = None, user: dict | None = None, tenant: str | None = None):
    """TestClient on `{slug}.ROOT` (or the apex), optionally signed in and bound to `tenant`."""
    host = f"{slug}.{ROOT}" if slug else ROOT
    client = TestClient(app, base_url=f"https://{host}")
    if user:
        bound = tenant if tenant is not None else slug
        extra = {"tenant": bound} if bound else {}
        client.cookies.set(SESSION_COOKIE, session_cookie(settings.session_secret, user, **extra), domain=host)
    return client


@pytest.fixture
def interviewer(app, settings):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings.session_secret, INTERVIEWER))
    return client


@pytest.fixture
def other_interviewer(app, settings):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings.session_secret, OTHER_INTERVIEWER))
    return client


@pytest.fixture
def created_session(interviewer):
    response = interviewer.post("/api/sessions", json={"candidate_name": "Ada Lovelace", "language": "python"})
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def candidate(app, created_session):
    client = TestClient(app)
    joined = client.post("/api/join", json={"pin": created_session["pin"]}).json()
    client.headers["Authorization"] = f"Bearer {joined['token']}"
    return client
