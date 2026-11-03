import re

from app.pins import generate_pin


def test_generate_pin_is_always_six_digits():
    for _ in range(2000):
        assert re.fullmatch(r"\d{6}", generate_pin())


def test_created_session_pins_are_unique(interviewer):
    pins = set()
    for i in range(60):
        body = interviewer.post("/api/sessions", json={"candidate_name": f"Candidate {i}"}).json()
        assert re.fullmatch(r"\d{6}", body["pin"])
        pins.add(body["pin"])
    assert len(pins) == 60


def test_pin_collision_retries(app):
    repo = app.state.repo
    pins = iter(["111111", "111111", "222222"])
    first = repo.create_session("A", "i@example.com", "python", {}, pin_factory=lambda: next(pins))
    second = repo.create_session("B", "i@example.com", "python", {}, pin_factory=lambda: next(pins))
    assert first["pin"] == "111111"
    assert second["pin"] == "222222"


def test_ended_session_frees_its_pin(app):
    repo = app.state.repo
    first = repo.create_session("A", "i@example.com", "python", {}, pin_factory=lambda: "333333")
    repo.set_status(first["id"], "ended", "tester")
    second = repo.create_session("B", "i@example.com", "python", {}, pin_factory=lambda: "333333")
    assert second["pin"] == "333333"


def test_create_session(interviewer):
    response = interviewer.post("/api/sessions", json={"candidate_name": "  Grace Hopper ", "language": "go"})
    assert response.status_code == 201
    body = response.json()
    assert body["candidate_name"] == "Grace Hopper"
    assert body["language"] == "go"
    assert body["status"] == "active"
    assert body["interviewer_email"] == "interviewer@example.com"


def test_create_session_validates_input(interviewer):
    assert interviewer.post("/api/sessions", json={"candidate_name": ""}).status_code == 422
    assert interviewer.post("/api/sessions", json={"candidate_name": "x", "language": "cobol"}).status_code == 422


def test_list_sessions_filters_by_candidate(interviewer, other_interviewer):
    for name in ("Ada Lovelace", "Alan Turing", "Ada Byron"):
        interviewer.post("/api/sessions", json={"candidate_name": name})
    other_interviewer.post("/api/sessions", json={"candidate_name": "Ada Elsewhere"})

    everything = interviewer.get("/api/sessions").json()
    assert {s["candidate_name"] for s in everything} == {"Ada Lovelace", "Alan Turing", "Ada Byron"}

    filtered = interviewer.get("/api/sessions", params={"candidate_name": "ada"}).json()
    assert {s["candidate_name"] for s in filtered} == {"Ada Lovelace", "Ada Byron"}


def test_get_session_detail_includes_files_and_runs(interviewer, created_session):
    detail = interviewer.get(f"/api/sessions/{created_session['id']}").json()
    assert detail["id"] == created_session["id"]
    assert [f["path"] for f in detail["files"]] == ["main.py"]
    assert detail["runs"] == []


def test_other_interviewer_cannot_read_session(other_interviewer, created_session):
    assert other_interviewer.get(f"/api/sessions/{created_session['id']}").status_code == 404
    assert other_interviewer.get(f"/api/sessions/{created_session['id']}/files").status_code == 404


def test_join_with_valid_pin(client, created_session):
    response = client.post("/api/join", json={"pin": created_session["pin"]})
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == created_session["id"]
    assert body["session"]["candidate_name"] == "Ada Lovelace"
    assert "interviewer_email" not in body["session"]
    assert body["token"]


def test_join_with_invalid_pin(client, created_session):
    wrong = "000000" if created_session["pin"] != "000000" else "999999"
    assert client.post("/api/join", json={"pin": wrong}).status_code == 404
    assert client.post("/api/join", json={"pin": "12ab56"}).status_code == 422
    assert client.post("/api/join", json={"pin": "123"}).status_code == 422


def test_join_rejected_after_session_ends(interviewer, client, created_session):
    interviewer.post(f"/api/sessions/{created_session['id']}/end")
    assert client.post("/api/join", json={"pin": created_session["pin"]}).status_code == 404


def test_candidate_token_is_scoped_to_one_session(interviewer, candidate):
    other = interviewer.post("/api/sessions", json={"candidate_name": "Someone Else"}).json()
    assert candidate.get(f"/api/sessions/{other['id']}/files").status_code == 404


def test_candidate_state_hides_pin(candidate, created_session, interviewer):
    sid = created_session["id"]
    candidate_state = candidate.get(f"/api/sessions/{sid}/state").json()
    assert candidate_state["role"] == "candidate"
    assert candidate_state["pin"] is None
    interviewer_state = interviewer.get(f"/api/sessions/{sid}/state").json()
    assert interviewer_state["role"] == "interviewer"
    assert interviewer_state["pin"] == created_session["pin"]
