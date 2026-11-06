import json

import pytest

from app.crypto import sign
from tests.fakes import CANDIDATE_ID, FakeApis, event, schedule_payload


@pytest.fixture
def fake(app):
    apis = FakeApis()
    app.state.http_transport = apis.transport()
    return apis


@pytest.fixture
def org(interviewer):
    return interviewer.post("/api/orgs", json={"name": "Acme"}).json()


def connect(interviewer, org, **settings):
    body = {"api_key": "ashby-key", "settings": {"candidate_link_field_id": "field-1", **settings}}
    assert interviewer.put(f"/api/orgs/{org['id']}/integrations/ashby", json=body).status_code == 200
    return interviewer.get(f"/api/orgs/{org['id']}/integrations/ashby/webhook-secret").json()


def deliver(client, hook, payload, secret=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Ashby-Signature": sign(secret or hook["webhook_secret"], raw)}
    return client.post(hook["webhook_url"].replace("http://testserver", ""), content=raw, headers=headers)


def org_sessions(app, org):
    return app.state.repo.list_org_sessions(org["id"], 100)


def test_rejects_unknown_org_and_bad_signature(client, interviewer, org, fake):
    hook = connect(interviewer, org)
    payload = schedule_payload("interviewScheduleCreate", [event("ev-1", ["panel@example.com"])])
    assert client.post("/integrations/ashby/webhook/nope", json=payload).status_code == 404
    assert deliver(client, hook, payload, secret="wrong").status_code == 401
    unsigned = client.post(hook["webhook_url"].replace("http://testserver", ""), json=payload)
    assert unsigned.status_code == 401


def test_schedule_creates_sessions_with_links(client, app, interviewer, org, fake):
    hook = connect(interviewer, org)
    payload = schedule_payload("interviewScheduleCreate", [event("ev-1", ["panel@example.com"]), event("ev-2", ["other@example.com"])])
    response = deliver(client, hook, payload)
    assert response.status_code == 200
    assert response.json()["created"] == 2

    sessions = org_sessions(app, org)
    assert len(sessions) == 2
    assert {s["candidate_name"] for s in sessions} == {"Ada Lovelace"}
    assert {s["interviewer_email"] for s in sessions} == {"panel@example.com", "other@example.com"}
    assert all(s["title"] == "Backend Engineer interview" for s in sessions)
    assert app.state.repo.member_role(org["id"], "panel@example.com") == "interviewer"

    field_calls = fake.named("customField.setValue")
    assert len(field_calls) == 2
    body = field_calls[0]["body"]
    assert body["objectType"] == "Candidate" and body["objectId"] == CANDIDATE_ID and body["fieldId"] == "field-1"
    assert body["fieldValue"].startswith("http://testserver/j/")
    notes = fake.named("candidate.createNote")
    assert len(notes) == 2 and "coding session ready" in notes[0]["body"]["note"]["value"]
    assert all(call["key"] == "ashby-key" for call in fake.calls)

    token = body["fieldValue"].rsplit("/", 1)[1]
    joined = client.post("/api/join/link", json={"token": token})
    assert joined.status_code == 200

    again = deliver(client, hook, schedule_payload("interviewScheduleUpdate", payload["data"]["interviewSchedule"]["interviewEvents"]))
    assert again.json()["created"] == 0
    assert len(org_sessions(app, org)) == 2


def test_stage_filter_and_non_scheduled_status(client, app, interviewer, org, fake):
    hook = connect(interviewer, org, interview_stage_ids=["stage-onsite"])
    skipped = deliver(client, hook, schedule_payload("interviewScheduleCreate", [event("ev-1", [])], stage="stage-1"))
    assert skipped.json()["skipped"] == 1
    pending = deliver(client, hook, schedule_payload("interviewScheduleCreate", [event("ev-1", [])], status="NeedsScheduling", stage="stage-onsite"))
    assert pending.json()["created"] == 0
    assert org_sessions(app, org) == []


def test_removed_and_cancelled_events_end_sessions(client, app, interviewer, org, fake):
    hook = connect(interviewer, org)
    deliver(client, hook, schedule_payload("interviewScheduleCreate", [event("ev-1", []), event("ev-2", [])]))
    removed = deliver(client, hook, schedule_payload("interviewScheduleUpdate", [event("ev-1", [])]))
    assert removed.json()["ended"] == 1
    statuses = sorted(s["status"] for s in org_sessions(app, org))
    assert statuses == ["active", "ended"]

    cancelled = deliver(client, hook, schedule_payload("interviewScheduleUpdate", [event("ev-1", [])], status="Cancelled"))
    assert cancelled.json()["ended"] == 1
    assert {s["status"] for s in org_sessions(app, org)} == {"ended"}
    assert fake.named("candidate.uploadFile") == []


def test_ignores_other_actions_and_records_last_event(client, app, interviewer, org, fake):
    hook = connect(interviewer, org)
    response = deliver(client, hook, {"action": "candidateHire", "data": {}})
    assert response.json() == {"ok": True, "ignored": "candidateHire"}
    assert app.state.repo.get_integration(org["id"], "ashby")["last_event_at"]


def test_api_errors_are_recorded_not_raised(client, app, interviewer, org, fake):
    hook = connect(interviewer, org)
    fake.fail.add("candidate.createNote")
    response = deliver(client, hook, schedule_payload("interviewScheduleCreate", [event("ev-1", [])]))
    assert response.status_code == 200 and response.json()["created"] == 1
    assert "createNote failed" in app.state.repo.get_integration(org["id"], "ashby")["last_error"]


def test_test_connection(interviewer, org, fake):
    connect(interviewer, org)
    result = interviewer.post(f"/api/orgs/{org['id']}/integrations/ashby/test").json()
    assert result == {"ok": True, "detail": "Connected as API key watchmecode"}


# Results write-back -----------------------------------------------------------------------


def create_linked_session(client, app, interviewer, org, fake, **settings):
    hook = connect(interviewer, org, **settings)
    deliver(client, hook, schedule_payload("interviewScheduleCreate", [event("ev-1", ["panel@example.com"])]))
    fake.calls.clear()
    return org_sessions(app, org)[0]


def test_ending_session_pushes_results_once(client, app, interviewer, org, fake):
    session = create_linked_session(client, app, interviewer, org, fake, add_completed_assessment=True)
    sid = session["id"]
    interviewer.put(f"/api/sessions/{sid}/files", json={"path": "main.py", "content": "print('done')\n"})
    interviewer.post(f"/api/sessions/{sid}/run", json={"entry_path": "main.py"})
    fake.ashby_users["interviewer@example.com"] = "ashby-user-me"

    assert interviewer.post(f"/api/sessions/{sid}/end").json()["status"] == "ended"
    note = fake.named("candidate.createNote")
    assert len(note) == 1
    assert note[0]["on_behalf_of"] == "ashby-user-me"
    assert "Runs:</strong> 1" in note[0]["body"]["note"]["value"]
    upload = fake.named("candidate.uploadFile")
    assert len(upload) == 1 and b'name="candidateId"' in upload[0]["multipart"] and b"application/zip" in upload[0]["multipart"]
    completed = fake.named("assessment.addCompletedToCandidate")[0]["body"]
    assert completed["candidateId"] == CANDIDATE_ID and completed["partnerId"] == "watchmecode"
    assert completed["assessment"]["assessmentId"] == sid
    assert {m["identifier"] for m in completed["assessment"]["metadata"]} >= {"runs", "language", "session"}

    interviewer.post(f"/api/sessions/{sid}/end")
    assert len(fake.named("candidate.createNote")) == 1


def test_settings_can_disable_write_back(client, app, interviewer, org, fake):
    session = create_linked_session(client, app, interviewer, org, fake, push_results_note=False, upload_files=False)
    interviewer.post(f"/api/sessions/{session['id']}/end")
    assert fake.calls == [c for c in fake.calls if c["endpoint"] == "user.search"]


def test_recording_stop_posts_playback_note(client, app, interviewer, org, fake):
    session = create_linked_session(client, app, interviewer, org, fake)
    sid = session["id"]
    rid = interviewer.post(f"/api/sessions/{sid}/recordings").json()["id"]
    interviewer.post(f"/api/sessions/{sid}/recordings/{rid}/stop")
    notes = fake.named("candidate.createNote")
    assert len(notes) == 1 and f"/#/s/{sid}/play/{rid}" in notes[0]["body"]["note"]["value"]
