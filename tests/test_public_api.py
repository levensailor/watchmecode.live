import base64
import io
import zipfile

import pytest


@pytest.fixture
def org(interviewer):
    return interviewer.post("/api/orgs", json={"name": "Acme"}).json()


@pytest.fixture
def api(client, interviewer, org):
    key = interviewer.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "scheduler"}).json()["key"]
    client.headers["Authorization"] = f"Bearer {key}"
    client.api_key = key
    return client


def test_requires_api_key(client):
    assert client.post("/v1/pads", json={"candidate_name": "A"}).status_code == 401
    assert client.get("/v1/pads", headers={"Authorization": "Bearer wmc_nope"}).status_code == 401


def test_create_pad_and_join_by_link(api, app, org, interviewer):
    created = api.post(
        "/v1/pads",
        json={"candidate_name": "Ada", "title": "Backend loop", "language": "go", "interviewer_email": "Panel@Example.com"},
    )
    assert created.status_code == 201
    pad = created.json()
    assert pad["mode"] == "live" and pad["title"] == "Backend loop" and pad["language"] == "go"
    assert pad["join_url"].startswith("http://testserver/j/")
    assert pad["interviewer_url"].endswith(f"/#/s/{pad['id']}")
    assert app.state.repo.member_role(org["id"], "panel@example.com") == "interviewer"

    token = pad["join_url"].rsplit("/", 1)[1]
    redirect = api.get(f"/j/{token}", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"] == f"/#/j/{token}"

    joined = api.post("/api/join/link", json={"token": token}).json()
    assert joined["session"]["id"] == pad["id"]
    candidate_headers = {"Authorization": f"Bearer {joined['token']}"}
    state = api.get(f"/api/sessions/{pad['id']}/state", headers=candidate_headers).json()
    assert state["role"] == "candidate"

    assert api.post("/api/join/link", json={"token": token + "x"}).status_code == 404
    interviewer.post(f"/api/sessions/{pad['id']}/end")
    assert api.post("/api/join/link", json={"token": token}).status_code == 410


def test_basic_auth_with_key_as_username(api):
    basic = base64.b64encode(f"{api.api_key}:".encode()).decode()
    response = api.get("/v1/pads", headers={"Authorization": f"Basic {basic}"})
    assert response.status_code == 200


def test_pads_are_scoped_to_org(api, other_interviewer):
    pad = api.post("/v1/pads", json={"candidate_name": "Ada"}).json()
    other_org = other_interviewer.post("/api/orgs", json={"name": "Other"}).json()
    other_key = other_interviewer.post(f"/api/orgs/{other_org['id']}/api-keys", json={"name": "k"}).json()["key"]
    assert api.get(f"/v1/pads/{pad['id']}", headers={"Authorization": f"Bearer {other_key}"}).status_code == 404


def test_end_pad_returns_results_and_zip(api):
    pad = api.post("/v1/pads", json={"candidate_name": "Ada", "files": {"main.py": "print('hi')\n", "lib/.keep": ""}}).json()
    token = pad["join_url"].rsplit("/", 1)[1]
    candidate = api.post("/api/join/link", json={"token": token}).json()["token"]
    run = api.post(f"/api/sessions/{pad['id']}/run", json={"entry_path": "main.py"}, headers={"Authorization": f"Bearer {candidate}"})
    assert run.json()["stdout"].strip() == "hi"

    assert api.get(f"/v1/pads/{pad['id']}").json()["results"] is None
    ended = api.post(f"/v1/pads/{pad['id']}/end").json()
    assert ended["status"] == "ended"
    results = ended["results"]
    assert results["run_count"] == 1 and results["file_count"] == 1
    assert results["last_run_exit_code"] == 0 and results["last_run_output"].strip() == "hi"
    assert results["files_url"].endswith(f"/api/sessions/{pad['id']}/files.zip")

    archive = api.get(f"/v1/pads/{pad['id']}/files.zip")
    assert archive.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert names == ["main.py"]
    assert api.get(f"/v1/pads/{pad['id']}/results").json()["session_id"] == pad["id"]


def test_take_home_pad_has_default_time_limit(api):
    pad = api.post("/v1/pads", json={"candidate_name": "Ada", "mode": "take_home"}).json()
    assert pad["mode"] == "take_home"
    session = api.get(f"/v1/pads/{pad['id']}").json()
    assert session["mode"] == "take_home"


def test_interviewer_zip_download(interviewer, created_session):
    response = interviewer.get(f"/api/sessions/{created_session['id']}/files.zip")
    assert response.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == ["main.py"]
