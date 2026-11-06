"""Lifecycle hooks that fan session events out to connected integrations.

Integration failures are logged and recorded on the integration; they never fail the user's request.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from app.integrations import ashby
from app.urls import playback_url

logger = logging.getLogger(__name__)


def session_finished(app: FastAPI, session_id: str, actor_email: str | None, base: str) -> None:
    repo = app.state.repo
    session = repo.get_session(session_id)
    if not session:
        return
    app.state.runner.release(session_id)
    for link in repo.links_for_session(session_id):
        if link["provider"] != ashby.PROVIDER:
            continue
        try:
            ashby.push_results(app, session, link, actor_email, base)
        except Exception:
            logger.exception("Pushing results to Ashby failed for session %s", session_id)


def recording_saved(app: FastAPI, session_id: str, recording: dict[str, Any], actor_email: str | None, base: str) -> None:
    repo = app.state.repo
    session = repo.get_session(session_id)
    if not session or session["status"] != "active":
        return
    for link in repo.links_for_session(session_id):
        if link["provider"] == ashby.PROVIDER and link["external_type"] == "interview_event":
            try:
                ashby.push_recording_note(app, session, link, playback_url(base, session_id, recording["id"]), actor_email)
            except Exception:
                logger.exception("Posting the recording note to Ashby failed for session %s", session_id)


def take_home_started(app: FastAPI, session_id: str) -> None:
    for link in app.state.repo.links_for_session(session_id):
        if link["provider"] == ashby.PROVIDER and link["external_type"] == "assessment":
            try:
                ashby.report_take_home_started(app, link)
            except Exception:
                logger.exception("Reporting take-home start to Ashby failed for session %s", session_id)
