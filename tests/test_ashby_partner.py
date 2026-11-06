import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import SESSION_COOKIE, create_app
from tests.conftest import INTERVIEWER, session_cookie
from tests.fakes import CANDIDATE_ID, FakeApis


@pytest.fixture
def settings_with_email(settings):
    return replace(settings, resend_api_key="re_test", email_from="Interviews <hiring@example.com>")


@pytest.fixture
def app(settings_with_email):
    return create_app(settings_with_email)


@pytest.fixture
def fake(app):
    apis = FakeApis()
    app.state.http_transport = apis.transport()
    return apis


@pytest.fixture
def admin(app, settings_with_email):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings_with_email.session_secret, INTERVIEWER))
    return client


@pytest.fixture
def setup(admin, fake):
    org = admin.post("/api/orgs", json={"name": "Acme"}).json()
    admin.put(f"/api/orgs/{org['id']}/integrations/ashby", json={"api_key": "ashby-key"})
    key = admin.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "Ashby"}).json()["key"]
    template = admin.post(
        f"/api/orgs/{org['id']}/assessments",
        json={"name": "Rate limiter", "description": "60 minute take-home", "language": "python", "time_limit_minutes": 30,
              "files": {"main.py": "def allow():\n    pass\n"}},
    ).json()
    return {"org": org, "key": key, "template": template}


def partner(client, key):
    token = base64.b64encode(f"{key}:".encode()).decode()
    client.headers["Authorization"] = f"Basic {token}"
    return client


def start_body(template_id):
    return {
        "assessment_type_id": template_id,
        "candidate": {"ashby_id": CANDIDATE_ID, "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
                      "ashby_profile_url": "https://app.ashbyhq.com/candidates/cand-1"},
        "application": {"ashby_id": "app-1", "status": "Active"},
        "job": {"ashby_id": "job-1", "name": "Backend Engineer", "ashby_job_url": "https://app.ashbyhq.com/jobs/job-1",
                "hiringTeam": [{"email": "panel@example.com", "firstName": "Pan", "lastName": "El", "role": "Hiring Manager", "userId": "u1"}]},
    }


def test_partner_endpoints_require_api_key(client):
    assert client.post("/partners/ashby/assessment.list", json={}).status_code == 401
    bad = partner(client, "wmc_wrong")
    assert bad.post("/partners/ashby/assessment.list", json={}).status_code == 401


def test_assessment_list(client, setup):
    response = partner(client, setup["key"]).post("/partners/ashby/assessment.list", json={}).json()
    assert response == {
        "success": True,
        "results": [{"assessment_type_id": setup["template"]["id"], "name": "Rate limiter", "description": "60 minute take-home"}],
    }
    assert partner(client, setup["key"]).post("/partners/ashby/customFields.fetch", json={}).json() == {"success": True, "fields": []}


def test_start_creates_take_home_and_emails_candidate(client, app, setup, fake):
    api = partner(client, setup["key"])
    response = api.post("/partners/ashby/assessment.start", json=start_body(setup["template"]["id"])).json()
    assert response["success"] is True
    assessment_id = response["results"]["assessment_id"]
    update = response["results"]["update_request"]
    assert update["assessment_status"] == {"identifier": "status", "label": "Status", "type": "string", "value": "Invited"}
    assert update["assessment_profile_url"]["value"].endswith(f"/#/s/{assessment_id}")
    link = update["metadata"][0]["value"]
    assert link.startswith("http://testserver/j/")

    session = app.state.repo.get_session(assessment_id)
    assert session["mode"] == "take_home" and session["time_limit_minutes"] == 30
    assert session["candidate_name"] == "Ada Lovelace" and session["interviewer_email"] == "panel@example.com"
    assert app.state.repo.list_files(assessment_id)[0]["content"] == "def allow():\n    pass\n"

    assert len(fake.emails) == 1
    email = fake.emails[0]
    assert email["to"] == ["ada@example.com"] and "Rate limiter" in email["subject"] and link in email["html"]

    again = api.post("/partners/ashby/assessment.start", json=start_body(setup["template"]["id"])).json()
    assert again["results"]["assessment_id"] == assessment_id
    assert len(fake.emails) == 1


def test_unknown_template_and_invalid_body(client, setup):
    api = partner(client, setup["key"])
    unknown = api.post("/partners/ashby/assessment.start", json=start_body("nope")).json()
    assert unknown["success"] is False and unknown["errorInfo"]["code"] == "unknown_assessment_type"
    invalid = api.post("/partners/ashby/assessment.start", json={"assessment_type_id": "x"}).json()
    assert invalid["success"] is False and invalid["errorInfo"]["code"] == "invalid_request"


def candidate_client(app, link):
    candidate = TestClient(app)
    token = link.rsplit("/", 1)[1]
    joined = candidate.post("/api/join/link", json={"token": token}).json()
    candidate.headers["Authorization"] = f"Bearer {joined['token']}"
    return candidate, joined["session"]["id"]


def test_take_home_lifecycle_reports_to_ashby(client, app, setup, fake):
    api = partner(client, setup["key"])
    started = api.post("/partners/ashby/assessment.start", json=start_body(setup["template"]["id"])).json()["results"]
    candidate, sid = candidate_client(app, started["update_request"]["metadata"][0]["value"])

    state = candidate.get(f"/api/sessions/{sid}/state").json()
    assert state["session"]["started_at"] and state["session"]["mode"] == "take_home"
    status_update = fake.named("assessment.update")[0]["body"]
    assert status_update["assessment_id"] == sid and status_update["assessment_status"]["value"] == "Started"

    candidate.put(f"/api/sessions/{sid}/files", json={"path": "main.py", "content": "print('ok')\n"})
    candidate.post(f"/api/sessions/{sid}/run", json={"entry_path": "main.py"})
    submitted = candidate.post(f"/api/sessions/{sid}/submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "ended" and submitted.json()["submitted_at"]

    result = fake.named("assessment.update")[-1]["body"]
    assert result["assessment_result"]["value"].startswith("Submitted · 1 runs")
    assert result["assessment_profile_url"]["type"] == "url"
    assert {m["identifier"] for m in result["metadata"]} >= {"runs", "files", "download", "duration"}
    upload = fake.named("candidate.uploadFile")
    assert len(upload) == 1 and CANDIDATE_ID.encode() in upload[0]["multipart"]
    assert candidate.get(f"/api/sessions/{sid}/state").status_code == 403


def test_time_limit_auto_submits(client, app, setup, fake):
    api = partner(client, setup["key"])
    started = api.post("/partners/ashby/assessment.start", json=start_body(setup["template"]["id"])).json()["results"]
    candidate, sid = candidate_client(app, started["update_request"]["metadata"][0]["value"])
    candidate.get(f"/api/sessions/{sid}/state")
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat(timespec="milliseconds")
    app.state.repo.update_session_fields(sid, started_at=past)

    response = candidate.get(f"/api/sessions/{sid}/changes")
    assert response.status_code == 403 and "Time is up" in response.json()["detail"]
    session = app.state.repo.get_session(sid)
    assert session["status"] == "ended" and session["submitted_at"]
    assert fake.named("assessment.update")[-1]["body"]["assessment_result"]


def test_cancel_ends_session_without_results(client, app, setup, fake):
    api = partner(client, setup["key"])
    started = api.post("/partners/ashby/assessment.start", json=start_body(setup["template"]["id"])).json()["results"]
    sid = started["assessment_id"]
    cancelled = api.post("/partners/ashby/assessment.cancel", json={"assessment_id": sid}).json()
    assert cancelled["success"] and cancelled["results"]["update_request"]["cancelled_reason"]["value"] == "Cancelled in Ashby"
    assert app.state.repo.get_session(sid)["status"] == "ended"
    assert fake.named("assessment.update") == [] and fake.named("candidate.uploadFile") == []
    missing = api.post("/partners/ashby/assessment.cancel", json={"assessment_id": "nope"}).json()
    assert missing["success"] is False


def test_live_sessions_cannot_be_submitted(admin, setup):
    session = admin.post("/api/sessions", json={"candidate_name": "X", "org_id": setup["org"]["id"]}).json()
    assert admin.post(f"/api/sessions/{session['id']}/submit").status_code == 409
