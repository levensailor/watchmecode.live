def files_of(client, sid):
    return {f["path"]: f["content"] for f in client.get(f"/api/sessions/{sid}/files").json()}


def test_file_crud(candidate, created_session):
    sid = created_session["id"]
    assert set(files_of(candidate, sid)) == {"main.py"}

    created = candidate.put(f"/api/sessions/{sid}/files", json={"path": "src/util.py", "content": "X = 1\n"})
    assert created.status_code == 200
    assert created.json()["path"] == "src/util.py"

    updated = candidate.put(f"/api/sessions/{sid}/files", json={"path": "src/util.py", "content": "X = 2\n"})
    assert updated.json()["content"] == "X = 2\n"
    assert files_of(candidate, sid)["src/util.py"] == "X = 2\n"

    renamed = candidate.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "src/util.py", "new_path": "src/helpers.py"})
    assert renamed.json() == {"renamed": {"src/util.py": "src/helpers.py"}}

    deleted = candidate.delete(f"/api/sessions/{sid}/files", params={"path": "src/helpers.py"})
    assert deleted.json() == {"deleted": ["src/helpers.py"]}
    assert set(files_of(candidate, sid)) == {"main.py"}


def test_folder_rename_and_delete(interviewer, created_session):
    sid = created_session["id"]
    for path in ("pkg/a.py", "pkg/b.py", "pkg/sub/c.py"):
        interviewer.put(f"/api/sessions/{sid}/files", json={"path": path, "content": ""})

    renamed = interviewer.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "pkg", "new_path": "lib"}).json()
    assert renamed["renamed"] == {"pkg/a.py": "lib/a.py", "pkg/b.py": "lib/b.py", "pkg/sub/c.py": "lib/sub/c.py"}

    deleted = interviewer.delete(f"/api/sessions/{sid}/files", params={"path": "lib"}).json()
    assert sorted(deleted["deleted"]) == ["lib/a.py", "lib/b.py", "lib/sub/c.py"]


def test_rename_conflict_and_missing(candidate, created_session):
    sid = created_session["id"]
    candidate.put(f"/api/sessions/{sid}/files", json={"path": "other.py", "content": ""})
    conflict = candidate.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "other.py", "new_path": "main.py"})
    assert conflict.status_code == 409
    missing = candidate.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "nope.py", "new_path": "x.py"})
    assert missing.status_code == 404
    assert candidate.delete(f"/api/sessions/{sid}/files", params={"path": "nope.py"}).status_code == 404


def test_paths_are_validated(candidate, created_session):
    sid = created_session["id"]
    for bad in ("../escape.py", "/abs.py", "a//b.py", "a/../b.py", "semi;colon.py"):
        response = candidate.put(f"/api/sessions/{sid}/files", json={"path": bad, "content": ""})
        assert response.status_code == 422, bad


def test_changes_feed_reports_other_participants_edits(interviewer, candidate, created_session):
    sid = created_session["id"]
    cursor = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": -1}).json()["cursor"]

    candidate.put(f"/api/sessions/{sid}/files", json={"path": "main.py", "content": "print('hi')\n"}, headers={"X-Client-Id": "cand-1"})
    candidate.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "main.py", "new_path": "app.py"})

    changes = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": cursor}).json()
    kinds = [e["kind"] for e in changes["events"]]
    assert kinds == ["file_upsert", "file_rename"]
    upsert = changes["events"][0]
    assert upsert["actor"] == "Ada Lovelace"
    assert upsert["client_id"] == "cand-1"
    assert upsert["data"]["content"] == "print('hi')\n"
    assert changes["cursor"] > cursor

    again = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": changes["cursor"]}).json()
    assert again["events"] == []
