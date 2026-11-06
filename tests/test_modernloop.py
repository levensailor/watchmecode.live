from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import SESSION_COOKIE, create_app
from tests.conftest import INTERVIEWER, session_cookie
from tests.fakes import FakeApis

CRON_SECRET = "cron-test-secret"


@pytest.fixture
def app(settings):
    return create_app(replace(settings, cron_secret=CRON_SECRET))


@pytest.fixture
def fake(app):
    apis = FakeApis()
    app.state.http_transport = apis.transport()
    return apis


@pytest.fixture
def admin(app, settings):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings.session_secret, INTERVIEWER))
    return client


@pytest.fixture
def org(admin, fake):
    org = admin.post("/api/orgs", json={"name": "Acme"}).json()
    response = admin.put(f"/api/orgs/{org['id']}/integrations/modernloop", json={"api_token": "ml-token-123", "settings": {"default_language": "go"}})
    assert response.status_code == 200
    return org


def ml_event(event_id, status="SCHEDULED", start_in=timedelta(days=1), candidate="can_1", stage="stage-1", debrief=False):
    start = datetime.now(UTC) + start_in
    return {
        "id": event_id,
        "schedule_id": "sch_1",
        "name": "Coding interview",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat(),
        "timezone": "UTC",
        "status": status,
        "application": {"id": "app_1", "candidate": {"id": candidate, "name": "Grace Hopper"}, "job": {"id": "job_1", "name": "SWE"}, "stage": {"id": stage, "name": "Onsite"}},
        "interviewers": [{"id": "usr_1", "name": "Pat", "role": "INTERVIEWER", "interview_module": None, "rsvp": "ACCEPTED"}],
        "video_meeting_url": None,
        "is_debrief": debrief,
        "created_at": start.isoformat(),
        "updated_at": start.isoformat(),
        "app_url": "https://app.modernloop.io/x",
    }


def seed(fake, events, ats_type="GREENHOUSE"):
    fake.ml_events = events
    fake.ml_candidates = {
        "can_1": {"id": "can_1", "name": {"full": "Grace Hopper"}, "primary_email": "grace@example.com", "ats": {"type": ats_type, "id": "a1"}},
    }
    fake.ml_users = {"usr_1": {"id": "usr_1", "name": "Pat", "email": "pat@example.com"}}


def sync(admin, org):
    response = admin.post(f"/api/orgs/{org['id']}/integrations/modernloop/sync")
    assert response.status_code == 200
    return response.json()


def test_sync_creates_sessions_and_is_idempotent(app, admin, org, fake):
    seed(fake, [ml_event("ev_1"), ml_event("ev_debrief", debrief=True), ml_event("ev_old", start_in=-timedelta(days=2))])
    result = sync(admin, org)
    assert result == {"created": 1, "ended": 0, "skipped": 2, "errors": []}

    sessions = app.state.repo.list_org_sessions(org["id"])
    assert len(sessions) == 1
    session = sessions[0]
    assert session["candidate_name"] == "Grace Hopper" and session["language"] == "go"
    assert session["interviewer_email"] == "pat@example.com" and session["title"] == "Coding interview"
    assert app.state.repo.member_role(org["id"], "pat@example.com") == "interviewer"
    link = app.state.repo.links_for_session(session["id"])[0]
    assert link["provider"] == "modernloop" and link["external_id"] == "ev_1" and link["data"]["join_url"].startswith("http://testserver/j/")

    calls = [c for c in fake.calls if c["endpoint"] == "ml:/v1/interview-events"]
    assert calls[0]["auth"] == "Bearer ml-token-123" and "updated_at_gte" in calls[0]["params"]

    first_synced = app.state.repo.get_integration(org["id"], "modernloop")["last_synced_at"]
    second = sync(admin, org)
    assert second["created"] == 0 and second["skipped"] == 3
    assert first_synced and [c for c in fake.calls if c["endpoint"] == "ml:/v1/interview-events"][-1]["params"]["updated_at_gte"] == first_synced


def test_cancelled_event_ends_session(app, admin, org, fake):
    seed(fake, [ml_event("ev_1")])
    sync(admin, org)
    seed(fake, [ml_event("ev_1", status="CANCELED")])
    assert sync(admin, org)["ended"] == 1
    assert app.state.repo.list_org_sessions(org["id"])[0]["status"] == "ended"


def test_skips_ashby_candidates_when_ashby_connected(app, admin, org, fake):
    admin.put(f"/api/orgs/{org['id']}/integrations/ashby", json={"api_key": "ashby-key"})
    seed(fake, [ml_event("ev_1")], ats_type="ASHBY")
    assert sync(admin, org)["created"] == 0
    admin.put(f"/api/orgs/{org['id']}/integrations/modernloop", json={"settings": {"skip_ashby_candidates": False}})
    assert sync(admin, org)["created"] == 1


def test_stage_filter(app, admin, org, fake):
    admin.put(f"/api/orgs/{org['id']}/integrations/modernloop", json={"settings": {"job_stage_ids": ["stage-onsite"]}})
    seed(fake, [ml_event("ev_1", stage="stage-1"), ml_event("ev_2", stage="stage-onsite")])
    result = sync(admin, org)
    assert result["created"] == 1
    assert app.state.repo.links_for_session(app.state.repo.list_org_sessions(org["id"])[0]["id"])[0]["external_id"] == "ev_2"


def test_errors_are_recorded(app, admin, org, fake):
    fake.ml_events = [ml_event("ev_1", candidate="can_missing")]
    fake.ml_candidates = {}

    app.state.http_transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "bad token"}))
    result = sync(admin, org)
    assert result["created"] == 0 and "rejected the API token" in result["errors"][0]
    assert "rejected" in app.state.repo.get_integration(org["id"], "modernloop")["last_error"]


def test_test_connection(admin, org, fake):
    assert admin.post(f"/api/orgs/{org['id']}/integrations/modernloop/test").json() == {
        "ok": True,
        "detail": "Connected to Acme as bot@modernloop.example",
    }


def test_cron_requires_secret_and_polls_all_orgs(app, admin, org, fake):
    seed(fake, [ml_event("ev_1")])
    client = TestClient(app)
    assert client.get("/api/cron/modernloop").status_code == 401
    assert client.get("/api/cron/modernloop", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/api/cron/modernloop", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert response.status_code == 200
    assert response.json() == {"orgs": 1, "created": 1, "ended": 0, "skipped": 0, "errors": 0}


def test_cron_disabled_without_secret(settings):
    client = TestClient(create_app(replace(settings, cron_secret="")))
    assert client.get("/api/cron/modernloop", headers={"Authorization": "Bearer x"}).status_code == 503


def test_pad_api_accepts_anonymous_candidate(admin, org, client):
    key = admin.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "ModernLoop"}).json()["key"]
    pad = client.post("/v1/pads", json={"title": "SWE - Onsite Coding (app_1)"}, headers={"Authorization": f"Bearer {key}"}).json()
    assert pad["candidate_name"] == "Candidate" and pad["title"] == "SWE - Onsite Coding (app_1)"
