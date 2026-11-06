"""Interview results: summary, final-code zip, and an HTML note for ATS write-back."""

from __future__ import annotations

import html
import io
import zipfile
from datetime import datetime
from typing import Any

from app.repository import Repository
from app.urls import files_url, interviewer_url, playback_url

KEEP_FILE = ".keep"
OUTPUT_PREVIEW_CHARS = 2000


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def build_summary(repo: Repository, base: str, session: dict[str, Any]) -> dict[str, Any]:
    session_id = session["id"]
    files = [f for f in repo.list_files(session_id) if not f["path"].endswith(KEEP_FILE)]
    runs = repo.list_runs(session_id, limit=1)
    last_run = runs[0] if runs else None
    recordings = repo.list_recordings(session_id)
    finished = recordings[0] if recordings and recordings[0]["ended_at"] else None

    ended_at = session.get("submitted_at") or (session["updated_at"] if session["status"] == "ended" else None)
    started = _parse(session.get("started_at") or session["created_at"])
    ended = _parse(ended_at)
    duration = round((ended - started).total_seconds() / 60, 1) if started and ended else None

    return {
        "session_id": session_id,
        "candidate_name": session["candidate_name"],
        "title": session.get("title"),
        "language": session["language"],
        "mode": session.get("mode") or "live",
        "status": session["status"],
        "created_at": session["created_at"],
        "ended_at": ended_at,
        "duration_minutes": duration,
        "file_count": len(files),
        "run_count": repo.run_count(session_id),
        "last_run_exit_code": last_run["exit_code"] if last_run else None,
        "last_run_entry": last_run["entry_path"] if last_run else None,
        "last_run_output": (last_run["stdout"] or last_run["stderr"])[:OUTPUT_PREVIEW_CHARS] if last_run else None,
        "interviewer_url": interviewer_url(base, session_id),
        "playback_url": playback_url(base, session_id, finished["id"]) if finished else None,
        "files_url": files_url(base, session_id),
    }


def build_zip(repo: Repository, session_id: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in repo.list_files(session_id):
            if not file["path"].endswith(KEEP_FILE):
                archive.writestr(file["path"], file["content"])
    return buffer.getvalue()


def zip_filename(session: dict[str, Any]) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in session["candidate_name"].lower()).strip("-") or "candidate"
    return f"watchmecode-{slug}-{session['id'][:8]}.zip"


def render_note(app_name: str, summary: dict[str, Any]) -> str:
    esc = html.escape
    rows = [
        ("Mode", "Take-home" if summary["mode"] == "take_home" else "Live interview"),
        ("Language", summary["language"]),
        ("Duration", f"{summary['duration_minutes']} min" if summary["duration_minutes"] is not None else "—"),
        ("Files", str(summary["file_count"])),
        ("Runs", str(summary["run_count"])),
    ]
    if summary["last_run_entry"]:
        rows.append(("Last run", f"{summary['last_run_entry']} (exit {summary['last_run_exit_code']})"))
    table = "".join(f"<li><strong>{esc(k)}:</strong> {esc(v)}</li>" for k, v in rows)
    links = [f'<a href="{esc(summary["interviewer_url"])}">Open session</a>']
    if summary["playback_url"]:
        links.append(f'<a href="{esc(summary["playback_url"])}">Watch playback</a>')
    output = ""
    if summary["last_run_output"]:
        output = f"<p><strong>Last output</strong></p><pre>{esc(summary['last_run_output'][:600])}</pre>"
    heading = esc(summary["title"] or f"{app_name} results")
    return f"<p><strong>{heading}</strong></p><ul>{table}</ul><p>{' · '.join(links)}</p>{output}"


def render_link_note(app_name: str, join_link: str, pin: str, interviewer_link: str, when: str | None) -> str:
    esc = html.escape
    scheduled = f" for {esc(when)}" if when else ""
    return (
        f"<p><strong>{esc(app_name)} coding session ready{scheduled}</strong></p>"
        f'<ul><li><strong>Candidate link:</strong> <a href="{esc(join_link)}">{esc(join_link)}</a> (PIN {esc(pin)})</li>'
        f'<li><strong>Interviewers:</strong> <a href="{esc(interviewer_link)}">open the session</a></li></ul>'
    )
