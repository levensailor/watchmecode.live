def put(client, sid, path, content):
    assert client.put(f"/api/sessions/{sid}/files", json={"path": path, "content": content}).status_code == 200


def test_recording_captures_only_its_window(interviewer, candidate, created_session):
    sid = created_session["id"]
    put(candidate, sid, "main.py", "before = True\n")

    started = interviewer.post(f"/api/sessions/{sid}/recordings")
    assert started.status_code == 201
    rid = started.json()["id"]
    assert started.json()["ended_at"] is None

    state = candidate.get(f"/api/sessions/{sid}/state").json()
    assert state["recording"]["id"] == rid

    put(candidate, sid, "main.py", "print('during')\n")
    candidate.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "main.py", "new_path": "app.py"})
    candidate.post(f"/api/sessions/{sid}/run", json={"entry_path": "app.py"})

    stopped = interviewer.post(f"/api/sessions/{sid}/recordings/{rid}/stop").json()
    assert stopped["ended_at"] is not None
    assert stopped["event_count"] == 3

    put(candidate, sid, "app.py", "after = True\n")

    playback = interviewer.get(f"/api/sessions/{sid}/recordings/{rid}").json()
    assert playback["initial_files"] == [{"path": "main.py", "content": "before = True\n"}]
    assert [e["kind"] for e in playback["events"]] == ["file_upsert", "file_rename", "run"]
    assert playback["events"][-1]["data"]["stdout"].strip() == "during"
    assert playback["session"]["candidate_name"] == "Ada Lovelace"
    assert candidate.get(f"/api/sessions/{sid}/state").json()["recording"] is None


def test_only_one_active_recording(interviewer, created_session):
    sid = created_session["id"]
    assert interviewer.post(f"/api/sessions/{sid}/recordings").status_code == 201
    assert interviewer.post(f"/api/sessions/{sid}/recordings").status_code == 409


def test_recordings_are_interviewer_only(candidate, other_interviewer, interviewer, created_session):
    sid = created_session["id"]
    rid = interviewer.post(f"/api/sessions/{sid}/recordings").json()["id"]
    assert candidate.post(f"/api/sessions/{sid}/recordings").status_code == 401
    assert candidate.get(f"/api/sessions/{sid}/recordings/{rid}").status_code == 401
    assert other_interviewer.get(f"/api/sessions/{sid}/recordings").status_code == 404
    assert other_interviewer.get(f"/api/sessions/{sid}/recordings/{rid}").status_code == 404


def test_ending_session_stops_recording(interviewer, created_session):
    sid = created_session["id"]
    interviewer.post(f"/api/sessions/{sid}/recordings")
    interviewer.post(f"/api/sessions/{sid}/end")
    recordings = interviewer.get(f"/api/sessions/{sid}/recordings").json()
    assert len(recordings) == 1
    assert recordings[0]["ended_at"] is not None
    assert interviewer.post(f"/api/sessions/{sid}/recordings").status_code == 409


def test_recording_events_reach_participants(interviewer, candidate, created_session):
    sid = created_session["id"]
    cursor = candidate.get(f"/api/sessions/{sid}/changes", params={"since": -1}).json()["cursor"]
    rid = interviewer.post(f"/api/sessions/{sid}/recordings").json()["id"]
    interviewer.post(f"/api/sessions/{sid}/recordings/{rid}/stop")
    events = candidate.get(f"/api/sessions/{sid}/changes", params={"since": cursor}).json()["events"]
    assert [(e["kind"], e["data"]["state"]) for e in events] == [("recording", "started"), ("recording", "stopped")]


def test_unknown_recording_is_404(interviewer, created_session):
    sid = created_session["id"]
    assert interviewer.get(f"/api/sessions/{sid}/recordings/nope").status_code == 404
    assert interviewer.post(f"/api/sessions/{sid}/recordings/nope/stop").status_code == 404
