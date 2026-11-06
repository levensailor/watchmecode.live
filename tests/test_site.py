from __future__ import annotations

import pytest

from tests.conftest import INTERVIEWER, ROOT, tenant_client

PAGES = ["/features", "/labs", "/integrations", "/pricing", "/customers", "/security"]


@pytest.mark.parametrize("path", PAGES)
def test_product_pages_render_in_single_mode(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "Start free trial" in response.text


def test_pricing_shows_plan_prices(client):
    html = client.get("/pricing").text
    assert "$249" in html and "$49" in html and "From $1K" in html
    assert 'data-monthly="$299"' in html


def test_single_mode_root_is_the_app(client):
    assert 'id="view-home"' in client.get("/").text


def test_apex_root_is_the_product_site(multi_app, multi_settings):
    html = tenant_client(multi_app, multi_settings).get("/").text
    assert "Coding interviews that feel like pairing." in html
    assert f'data-legacy-app="https://app.{ROOT}"' in html


def test_workspace_root_is_the_app_and_product_pages_redirect(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    acme = tenant_client(multi_app, multi_settings, "acme")
    assert 'id="view-home"' in acme.get("/").text
    redirect = acme.get("/pricing", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == f"https://{ROOT}/pricing"


def test_signup_and_login_pages_on_the_apex(multi_app, multi_settings):
    hub = tenant_client(multi_app, multi_settings)
    assert 'id="signup-form"' in hub.get("/signup").text
    login = hub.get("/login?error=not_member&tenant=acme").text
    assert "not a member of <strong>acme</strong>" in login


def test_apex_login_remembers_next(multi_app, multi_settings):
    hub = tenant_client(multi_app, multi_settings)
    response = hub.get("/auth/google?next=/signup", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "accounts.google.com" in response.headers["location"]
