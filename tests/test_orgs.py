from fastapi.testclient import TestClient

from app.main import SESSION_COOKIE
from tests.conftest import INTERVIEWER, OTHER_INTERVIEWER, session_cookie


def make_org(client, name="Acme Hiring"):
    response = client.post("/api/orgs", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_create_and_list_orgs(interviewer):
    org = make_org(interviewer)
    assert org["slug"] == "acme-hiring"
    assert org["role"] == "admin"
    assert [o["id"] for o in interviewer.get("/api/orgs").json()] == [org["id"]]
    second = make_org(interviewer)
    assert second["slug"].startswith("acme-hiring-") and second["slug"] != org["slug"]


def test_org_detail_requires_membership(interviewer, other_interviewer):
    org = make_org(interviewer)
    assert other_interviewer.get(f"/api/orgs/{org['id']}").status_code == 404
    detail = interviewer.get(f"/api/orgs/{org['id']}").json()
    assert detail["members"] == [{"email": INTERVIEWER["email"], "role": "admin", "created_at": detail["members"][0]["created_at"]}]
    assert {i["provider"] for i in detail["integrations"]} == {"ashby", "modernloop"}
    assert detail["integrations"][0]["webhook_url"].endswith(f"/integrations/ashby/webhook/{org['slug']}")


def test_members_and_admin_rules(interviewer, other_interviewer):
    org = make_org(interviewer)
    oid = org["id"]
    added = interviewer.post(f"/api/orgs/{oid}/members", json={"email": OTHER_INTERVIEWER["email"], "role": "interviewer"})
    assert added.status_code == 201
    assert other_interviewer.get(f"/api/orgs/{oid}").json()["role"] == "interviewer"
    assert other_interviewer.post(f"/api/orgs/{oid}/members", json={"email": "x@example.com"}).status_code == 403
    assert interviewer.delete(f"/api/orgs/{oid}/members/{INTERVIEWER['email']}").status_code == 409
    assert interviewer.post(f"/api/orgs/{oid}/members", json={"email": "bad-email"}).status_code == 422
    assert interviewer.delete(f"/api/orgs/{oid}/members/{OTHER_INTERVIEWER['email']}").status_code == 204
    assert other_interviewer.get(f"/api/orgs/{oid}").status_code == 404


def test_api_keys_are_shown_once_and_revocable(interviewer, client):
    org = make_org(interviewer)
    created = interviewer.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "ModernLoop"}).json()
    assert created["key"].startswith("wmc_") and created["prefix"] == created["key"][:12]
    listed = interviewer.get(f"/api/orgs/{org['id']}/api-keys").json()
    assert [k["id"] for k in listed] == [created["id"]] and "key" not in listed[0]
    headers = {"Authorization": f"Bearer {created['key']}"}
    assert client.get("/v1/pads", headers=headers).status_code == 200
    assert interviewer.delete(f"/api/orgs/{org['id']}/api-keys/{created['id']}").status_code == 204
    assert client.get("/v1/pads", headers=headers).status_code == 401


def test_org_sessions_are_shared_with_members(app, settings, interviewer, other_interviewer):
    org = make_org(interviewer)
    oid = org["id"]
    assert other_interviewer.post("/api/sessions", json={"candidate_name": "X", "org_id": oid}).status_code == 404
    session = interviewer.post("/api/sessions", json={"candidate_name": "Grace", "org_id": oid}).json()
    assert session["org_id"] == oid and session["mode"] == "live"
    assert other_interviewer.get(f"/api/sessions/{session['id']}").status_code == 404

    interviewer.post(f"/api/orgs/{oid}/members", json={"email": OTHER_INTERVIEWER["email"]})
    assert other_interviewer.get(f"/api/sessions/{session['id']}").status_code == 200
    assert other_interviewer.get(f"/api/sessions/{session['id']}/state").json()["role"] == "interviewer"
    listed = other_interviewer.get("/api/sessions", params={"org_id": oid}).json()
    assert [s["id"] for s in listed] == [session["id"]]


def test_integration_credentials_are_encrypted(app, interviewer):
    org = make_org(interviewer)
    oid = org["id"]
    assert interviewer.put(f"/api/orgs/{oid}/integrations/ashby", json={}).status_code == 422
    connected = interviewer.put(
        f"/api/orgs/{oid}/integrations/ashby",
        json={"api_key": "ashby-secret-key", "settings": {"interview_stage_ids": ["stage-1"], "upload_files": False}},
    ).json()
    assert connected["connected"] and connected["settings"]["interview_stage_ids"] == ["stage-1"]
    row = app.state.repo.get_integration(oid, "ashby")
    assert "ashby-secret-key" not in row["credentials"]
    assert app.state.vault.decrypt(row["credentials"]) == "ashby-secret-key"

    secret = interviewer.get(f"/api/orgs/{oid}/integrations/ashby/webhook-secret").json()
    kept = interviewer.put(f"/api/orgs/{oid}/integrations/ashby", json={"settings": {}}).json()
    assert kept["connected"]
    assert interviewer.get(f"/api/orgs/{oid}/integrations/ashby/webhook-secret").json() == secret
    interviewer.put(f"/api/orgs/{oid}/integrations/ashby", json={"rotate_webhook_secret": True})
    assert interviewer.get(f"/api/orgs/{oid}/integrations/ashby/webhook-secret").json()["webhook_secret"] != secret["webhook_secret"]
    assert interviewer.delete(f"/api/orgs/{oid}/integrations/ashby").status_code == 204
    assert interviewer.get(f"/api/orgs/{oid}/integrations/ashby/webhook-secret").status_code == 404


def test_templates_crud(interviewer):
    org = make_org(interviewer)
    oid = org["id"]
    created = interviewer.post(f"/api/orgs/{oid}/assessments", json={"name": "Two Sum", "language": "javascript", "time_limit_minutes": 45})
    assert created.status_code == 201
    template = created.json()
    assert template["files"].keys() == {"index.js"}
    updated = interviewer.put(
        f"/api/orgs/{oid}/assessments/{template['id']}",
        json={"name": "Two Sum v2", "language": "python", "files": {"main.py": "print(1)\n"}},
    ).json()
    assert updated["name"] == "Two Sum v2" and updated["files"] == {"main.py": "print(1)\n"}
    assert [t["name"] for t in interviewer.get(f"/api/orgs/{oid}/assessments").json()] == ["Two Sum v2"]
    assert interviewer.delete(f"/api/orgs/{oid}/assessments/{template['id']}").status_code == 204


def test_org_routes_require_sign_in(client):
    assert client.get("/api/orgs").status_code == 401
    assert client.post("/api/orgs", json={"name": "Nope"}).status_code == 401


def test_cookie_helper_round_trip(app, settings):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings.session_secret, OTHER_INTERVIEWER))
    assert client.get("/api/me").json()["email"] == OTHER_INTERVIEWER["email"]
