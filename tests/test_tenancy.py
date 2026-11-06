from __future__ import annotations

from app.repository import Repository
from app.tenancy import schema_for, slug_error
from app.urls import base_url
from tests.conftest import INTERVIEWER, OTHER_INTERVIEWER, ROOT, tenant_client


def provision(app, slug, owner=INTERVIEWER["email"], plan="trial"):
    return app.state.tenants.provision(slug=slug, name=slug.title(), owner_email=owner, plan=plan)


def test_default_tenant_is_created_and_open(multi_app):
    tenant = multi_app.state.tenants.default_tenant()
    assert tenant["slug"] == "app"
    assert tenant["open_access"] is True
    assert tenant["schema_name"] == "watchmecode"


def test_unknown_subdomain_is_404_with_signup_link(multi_app, multi_settings):
    response = tenant_client(multi_app, multi_settings, "nobody").get("/")
    assert response.status_code == 404
    assert f"https://{ROOT}/signup" in response.text


def test_apex_and_tenant_hosts_resolve(multi_app, multi_settings):
    provision(multi_app, "acme")
    assert tenant_client(multi_app, multi_settings).get("/api/health").json()["mode"] == "multi"
    assert tenant_client(multi_app, multi_settings, "acme").get("/api/health").status_code == 200


def test_sessions_are_isolated_between_workspaces(multi_app, multi_settings):
    provision(multi_app, "acme")
    provision(multi_app, "globex")
    acme = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    globex = tenant_client(multi_app, multi_settings, "globex", INTERVIEWER)

    created = acme.post("/api/sessions", json={"candidate_name": "Ada", "language": "python"})
    assert created.status_code == 201
    session = created.json()

    assert [s["id"] for s in acme.get("/api/sessions").json()] == [session["id"]]
    assert globex.get("/api/sessions").json() == []
    assert globex.get(f"/api/sessions/{session['id']}").status_code in (403, 404)

    pin = session["pin"]
    assert tenant_client(multi_app, multi_settings, "globex").post("/api/join", json={"pin": pin}).status_code == 404
    assert tenant_client(multi_app, multi_settings, "acme").post("/api/join", json={"pin": pin}).status_code == 200


def test_session_cookie_is_bound_to_its_workspace(multi_app, multi_settings):
    provision(multi_app, "acme")
    provision(multi_app, "globex")
    replayed = tenant_client(multi_app, multi_settings, "globex", INTERVIEWER, tenant="acme")
    assert replayed.get("/api/me").status_code == 401


def test_non_members_are_refused_until_their_domain_is_allowed(multi_app, multi_settings):
    tenant = provision(multi_app, "acme")
    outsider = tenant_client(multi_app, multi_settings, "acme", OTHER_INTERVIEWER)
    assert outsider.get("/api/me").status_code == 403

    registry = multi_app.state.tenants
    registry.platform.update_tenant(tenant["id"], allowed_domains=["example.com"])
    registry.invalidate("acme")
    assert outsider.get("/api/me").status_code == 200


def test_links_use_the_workspace_host(multi_app, multi_settings):
    tenant = provision(multi_app, "acme")
    with multi_app.state.tenants.use(tenant):
        assert base_url(multi_settings) == f"https://acme.{ROOT}"
    assert base_url(multi_settings) == f"https://{ROOT}"


def test_apex_join_link_redirects_to_default_workspace(multi_app, multi_settings):
    response = tenant_client(multi_app, multi_settings).get("/j/abc", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"https://app.{ROOT}/#/j/abc"


def test_each_workspace_gets_its_own_schema_and_version(multi_app):
    tenant = provision(multi_app, "acme-corp")
    assert tenant["schema_name"] == schema_for("acme-corp") == "t_acme_corp"
    assert tenant["schema_version"] == Repository.schema_version()
    results = multi_app.state.tenants.migrate_all(force=True)
    assert {r["slug"] for r in results} == {"app", "acme-corp"}


def test_slug_rules(multi_settings):
    assert slug_error(multi_settings, "ae") is None
    assert slug_error(multi_settings, "american-eagle") is None
    assert slug_error(multi_settings, "a") is not None
    assert slug_error(multi_settings, "1abc") is not None
    assert slug_error(multi_settings, "www") == "That subdomain is reserved."
    assert slug_error(multi_settings, "app") == "That subdomain is reserved."
    assert slug_error(multi_settings, "bad--slug") is not None
