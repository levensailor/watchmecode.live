from __future__ import annotations

import pytest

from app.collab import ChangeSetError, apply_changes, normalize_newlines, replacement
from app.routes import realtime_config


def test_apply_changes_follows_codemirror_json():
    assert apply_changes("hello world", [6, [5, "there"]]) == "hello there"
    assert apply_changes("ab", [1, [0, "x", "y"], 1]) == "ax\nyb"
    assert apply_changes("abc", [[3]]) == ""
    assert apply_changes("", [[0, "new"]]) == "new"


def test_apply_changes_counts_utf16_units():
    text = "a😀b"  # the emoji is 2 UTF-16 units
    assert apply_changes(text, [3, [1, "c"]]) == "a😀c"
    with pytest.raises(ChangeSetError):
        apply_changes(text, [2, [2]])  # splits the surrogate pair
    with pytest.raises(ChangeSetError):
        apply_changes(text, [3])  # too short


@pytest.mark.parametrize("bad", [[5, 5], [[-1]], ["x"], [[1, 2]], "nope", [True]])
def test_apply_changes_rejects_malformed(bad):
    with pytest.raises(ChangeSetError):
        apply_changes("hello", bad)


@pytest.mark.parametrize(
    ("old", "new"),
    [("hello world", "hello there"), ("", "abc"), ("abc", ""), ("a😀b", "a😃b"), ("line1\nline2", "line1\nX\nline2"), ("same", "same")],
)
def test_replacement_round_trips(old, new):
    assert apply_changes(old, replacement(old, new)) == new


def test_normalize_newlines():
    assert normalize_newlines("a\r\nb\rc") == "a\nb\nc"


def push(client, sid, path, version, changes, client_id="c1"):
    return client.post(
        f"/api/sessions/{sid}/collab",
        json={"path": path, "version": version, "updates": [{"clientID": client_id, "changes": changes}]},
        headers={"X-Client-Id": client_id},
    )


def test_push_and_pull_with_versions(interviewer, created_session, candidate):
    sid = created_session["id"]
    files = {f["path"]: f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"]}
    main = files["main.py"]
    assert main["version"] == 0
    length = len(main["content"].encode("utf-16-le")) // 2

    ok = push(interviewer, sid, "main.py", 0, [[0, "# hi", ""], length]).json()
    assert ok == {"ok": True, "version": 1}

    stale = push(candidate, sid, "main.py", 0, [length, [0, "\n# bye"]], client_id="c2").json()
    assert stale == {"ok": False, "version": 1}

    pulled = candidate.get(f"/api/sessions/{sid}/collab", params={"path": "main.py", "since": 0}).json()
    assert pulled["version"] == 1 and not pulled["resync"]
    assert pulled["updates"] == [{"version": 1, "clientID": "c1", "changes": [[0, "# hi", ""], length]}]

    rebased = push(candidate, sid, "main.py", 1, [length + 5, [0, "\n# bye"]], client_id="c2").json()
    assert rebased == {"ok": True, "version": 2}
    content = {f["path"]: f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"]}["main.py"]
    assert content["content"].startswith("# hi\n") and content["content"].endswith("\n# bye")
    assert content["version"] == 2


def test_push_emits_file_events_with_versions(interviewer, created_session):
    sid = created_session["id"]
    cursor = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": -1}).json()["cursor"]
    main = next(f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"] if f["path"] == "main.py")
    push(interviewer, sid, "main.py", 0, [[0, "x"], len(main["content"].encode("utf-16-le")) // 2])
    events = interviewer.get(f"/api/sessions/{sid}/changes", params={"since": cursor}).json()["events"]
    assert events[-1]["kind"] == "file_upsert"
    assert events[-1]["data"]["version"] == 1 and events[-1]["data"]["content"].startswith("x")
    assert events[-1]["client_id"] == "c1"


def test_bad_change_sets_are_rejected(interviewer, created_session):
    sid = created_session["id"]
    assert push(interviewer, sid, "main.py", 0, [99999]).status_code == 422
    assert push(interviewer, sid, "missing.py", 0, [0]).status_code == 404


def test_whole_file_writes_log_a_replacement_for_collab_clients(interviewer, created_session):
    sid = created_session["id"]
    interviewer.put(f"/api/sessions/{sid}/files", json={"path": "main.py", "content": "print('replaced')\r\n"})
    pulled = interviewer.get(f"/api/sessions/{sid}/collab", params={"path": "main.py", "since": 0}).json()
    assert pulled["version"] == 1
    assert len(pulled["updates"]) == 1 and pulled["updates"][0]["clientID"] == "server"
    detail = next(f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"] if f["path"] == "main.py")
    assert detail["content"] == "print('replaced')\n"

    created = interviewer.put(f"/api/sessions/{sid}/files", json={"path": "new.py", "content": "x = 1\n"}).json()
    assert created["version"] == 0


def test_pull_ignores_updates_newer_than_the_version_it_read(app, interviewer, created_session):
    """A push can commit between reading the file and its log; the pull must stay contiguous."""
    sid = created_session["id"]
    main = next(f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"] if f["path"] == "main.py")
    push(interviewer, sid, "main.py", 0, [[0, "a"], len(main["content"].encode("utf-16-le")) // 2])
    with app.state.repo.connect() as conn:
        conn.run(
            "INSERT INTO doc_updates (session_id, path, version, client_id, changes, created_at) VALUES (?, ?, 2, 'x', '[]', 'now')",
            (sid, "main.py"),
        )
    pulled = interviewer.get(f"/api/sessions/{sid}/collab", params={"path": "main.py", "since": 0}).json()
    assert pulled["resync"] is False and [u["version"] for u in pulled["updates"]] == [1]


def test_pull_resyncs_when_history_is_missing(interviewer, created_session):
    sid = created_session["id"]
    resync = interviewer.get(f"/api/sessions/{sid}/collab", params={"path": "main.py", "since": 5}).json()
    assert resync["resync"] is True and resync["content"]


def test_rename_and_delete_keep_history_aligned(interviewer, created_session):
    sid = created_session["id"]
    main = next(f for f in interviewer.get(f"/api/sessions/{sid}").json()["files"] if f["path"] == "main.py")
    push(interviewer, sid, "main.py", 0, [[0, "# a", ""], len(main["content"].encode("utf-16-le")) // 2])
    interviewer.post(f"/api/sessions/{sid}/files/rename", json={"old_path": "main.py", "new_path": "app.py"})
    moved = interviewer.get(f"/api/sessions/{sid}/collab", params={"path": "app.py", "since": 0}).json()
    assert moved["version"] == 1 and len(moved["updates"]) == 1
    interviewer.delete(f"/api/sessions/{sid}/files", params={"path": "app.py"})
    interviewer.put(f"/api/sessions/{sid}/files", json={"path": "app.py", "content": "fresh"})
    fresh = interviewer.get(f"/api/sessions/{sid}/collab", params={"path": "app.py", "since": 0}).json()
    assert fresh == {"version": 0, "updates": [], "resync": False, "content": None}


def test_realtime_topic_is_secret_and_stable(settings):
    from dataclasses import replace

    assert realtime_config(settings, "abc") is None
    configured = replace(settings, supabase_url="https://x.supabase.co/", supabase_publishable_key="sb_publishable_x")
    first = realtime_config(configured, "abc")
    assert first == realtime_config(configured, "abc")
    assert first["url"] == "https://x.supabase.co" and first["topic"].startswith("wmc-")
    assert realtime_config(configured, "abd")["topic"] != first["topic"]
    assert "abc" not in first["topic"]
