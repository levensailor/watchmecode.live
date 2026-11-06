from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app import handoff
from app.main import create_app
from tests.conftest import INTERVIEWER, ROOT, tenant_client


def register_dedicated(app, slug="bigco", domain="code.bigco.test"):
    platform = app.state.tenants.platform
    tenant = platform.create_tenant(
        slug=slug, name="BigCo", owner_email=INTERVIEWER["email"], schema_name="dedicated", plan="enterprise",
        status="active", deployment="dedicated",
    )
    platform.update_tenant(tenant["id"], custom_domain=domain)
    app.state.tenants.invalidate(slug)
    return tenant


def test_shared_deployment_never_serves_a_dedicated_workspace(multi_app, multi_settings):
    register_dedicated(multi_app)
    assert tenant_client(multi_app, multi_settings, "bigco").get("/api/health").status_code == 404
    assert all(t["slug"] != "bigco" for t in multi_app.state.tenants.active_tenants())


def test_hub_hands_off_to_the_dedicated_domain(multi_app, multi_settings):
    register_dedicated(multi_app)
    go = tenant_client(multi_app, multi_settings, None, INTERVIEWER).get("/api/platform/go/bigco", follow_redirects=False)
    location = urlsplit(go.headers["location"])
    assert (location.scheme, location.netloc, location.path) == ("https", "code.bigco.test", "/auth/handoff")


def test_dedicated_instance_signs_in_through_the_hub(settings, multi_settings):
    instance_settings = replace(
        settings,
        tenant_mode="dedicated",
        tenant_slug="bigco",
        public_url="https://code.bigco.test",
        auth_hub_url=f"https://{ROOT}",
        session_secret="instance-secret",
        google_client_id="",
        google_client_secret="",
        handoff_public_key=handoff.public_key_b64(multi_settings),
    )
    instance = TestClient(create_app(instance_settings), base_url="https://code.bigco.test")
    assert instance.get("/api/info").json()["google_enabled"] is True

    login = instance.get("/auth/google?next=/", follow_redirects=False)
    assert login.headers["location"] == f"https://{ROOT}/auth/google?tenant=bigco&next=%2F"

    token = handoff.issue(multi_settings, INTERVIEWER, "bigco", "https://code.bigco.test")
    finished = instance.get(f"/auth/handoff?token={token}", follow_redirects=False)
    assert finished.status_code == 303
    assert instance.get("/api/me").json()["email"] == INTERVIEWER["email"]

    stolen = handoff.issue(multi_settings, INTERVIEWER, "other", "https://code.bigco.test")
    assert instance.get(f"/auth/handoff?token={stolen}").status_code == 401
