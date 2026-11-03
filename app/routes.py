"""Session, file, run, join, and change-feed endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.auth import (
    Participant,
    bearer_token,
    issue_participant_token,
    read_participant_token,
    require_user,
    session_user,
)
from app.languages import LANGUAGES
from app.repository import PathConflictError, PinExhaustedError, Repository
from app.runner import CodeRunner
from app.schemas import (
    AppInfoOut,
    ChangesOut,
    DeleteOut,
    FileOut,
    FilePath,
    FileRename,
    FileUpsert,
    JoinIn,
    JoinOut,
    LanguageOut,
    RenameOut,
    RunIn,
    RunOut,
    SessionCreate,
    SessionDetail,
    SessionOut,
    SessionPublic,
    SessionState,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ClientId = Annotated[str | None, Header(alias="X-Client-Id", max_length=64)]


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_runner(request: Request) -> CodeRunner:
    return request.app.state.runner


def _owned_session(repo: Repository, session_id: str, user: dict) -> dict:
    session = repo.get_session(session_id)
    if not session or session["interviewer_email"] != user["email"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


def participant(session_id: str, request: Request, repo: Repository = Depends(get_repo)) -> Participant:
    """Allow the owning interviewer (cookie) or a candidate holding a PIN-issued token."""
    session = repo.get_session(session_id)
    user = session_user(request)
    if session and user and session["interviewer_email"] == user["email"]:
        return Participant("interviewer", user.get("name") or user["email"], session_id)
    token = bearer_token(request)
    if token:
        sid = read_participant_token(request.app.state.settings, token)
        if sid == session_id and session:
            if session["status"] != "active":
                raise HTTPException(status.HTTP_403_FORBIDDEN, "This session has ended")
            return Participant("candidate", session["candidate_name"], session_id)
    if not user and not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in or join with a PIN")
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")


# Meta ------------------------------------------------------------------------


@router.get("/api/info", response_model=AppInfoOut, tags=["meta"])
def app_info(request: Request, repo: Repository = Depends(get_repo)) -> AppInfoOut:
    settings = request.app.state.settings
    return AppInfoOut(
        app_name=settings.app_name,
        tagline=settings.app_tagline,
        author=settings.app_author,
        google_enabled=settings.google_configured,
        storage=repo.kind,
    )


@router.get("/api/languages", response_model=list[LanguageOut], tags=["meta"])
def list_languages() -> list[LanguageOut]:
    return [
        LanguageOut(id=lang.id, label=lang.label, entry=lang.entry, extensions=list(lang.extensions))
        for lang in LANGUAGES.values()
    ]


# Interviewer -------------------------------------------------------------------


@router.post("/api/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED, tags=["interviewer"])
def create_session(body: SessionCreate, user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> dict:
    language = LANGUAGES[body.language]
    try:
        return repo.create_session(body.candidate_name.strip(), user["email"], language.id.value, dict(language.starter))
    except PinExhaustedError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/api/sessions", response_model=list[SessionOut], tags=["interviewer"])
def list_sessions(
    candidate_name: Annotated[str | None, Query(max_length=80)] = None,
    user: dict = Depends(require_user),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return repo.list_sessions(user["email"], candidate_name)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail, tags=["interviewer"])
def get_session(session_id: str, user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> dict:
    session = _owned_session(repo, session_id, user)
    return {**session, "files": repo.list_files(session_id), "runs": repo.list_runs(session_id)}


@router.post("/api/sessions/{session_id}/end", response_model=SessionOut, tags=["interviewer"])
def end_session(session_id: str, user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> dict:
    _owned_session(repo, session_id, user)
    return repo.set_status(session_id, "ended", user.get("name") or user["email"])


# Shared ------------------------------------------------------------------------


@router.post("/api/join", response_model=JoinOut, tags=["shared"])
def join(body: JoinIn, request: Request, repo: Repository = Depends(get_repo)) -> JoinOut:
    session = repo.get_active_session_by_pin(body.pin)
    if not session:
        logger.info("Join rejected for an unknown PIN")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active session for that PIN")
    logger.info("Candidate joined session %s", session["id"])
    return JoinOut(
        session=SessionPublic(**session),
        token=issue_participant_token(request.app.state.settings, session["id"]),
    )


@router.get("/api/sessions/{session_id}/state", response_model=SessionState, tags=["shared"])
def session_state(
    session_id: str, who: Participant = Depends(participant), repo: Repository = Depends(get_repo)
) -> dict:
    cursor = repo.latest_cursor(session_id)
    session = repo.get_session(session_id)
    return {
        "session": session,
        "role": who.role,
        "pin": session["pin"] if who.role == "interviewer" else None,
        "files": repo.list_files(session_id),
        "runs": list(reversed(repo.list_runs(session_id, limit=20))),
        "cursor": cursor,
    }


@router.get("/api/sessions/{session_id}/files", response_model=list[FileOut], tags=["shared"])
def list_files(session_id: str, _: Participant = Depends(participant), repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_files(session_id)


@router.put("/api/sessions/{session_id}/files", response_model=FileOut, tags=["shared"])
def upsert_file(
    session_id: str,
    body: FileUpsert,
    client_id: ClientId = None,
    who: Participant = Depends(participant),
    repo: Repository = Depends(get_repo),
) -> dict:
    return repo.upsert_file(session_id, body.path, body.content, who.label, client_id)


@router.delete("/api/sessions/{session_id}/files", response_model=DeleteOut, tags=["shared"])
def delete_file(
    session_id: str,
    path: Annotated[FilePath, Query(description="File or folder path")],
    client_id: ClientId = None,
    who: Participant = Depends(participant),
    repo: Repository = Depends(get_repo),
) -> DeleteOut:
    deleted = repo.delete_path(session_id, path, who.label, client_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return DeleteOut(deleted=deleted)


@router.post("/api/sessions/{session_id}/files/rename", response_model=RenameOut, tags=["shared"])
def rename_file(
    session_id: str,
    body: FileRename,
    client_id: ClientId = None,
    who: Participant = Depends(participant),
    repo: Repository = Depends(get_repo),
) -> RenameOut:
    try:
        mapping = repo.rename_path(session_id, body.old_path, body.new_path, who.label, client_id)
    except PathConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not mapping:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return RenameOut(renamed=mapping)


@router.post("/api/sessions/{session_id}/run", response_model=RunOut, tags=["shared"])
def run_code(
    session_id: str,
    body: RunIn,
    client_id: ClientId = None,
    who: Participant = Depends(participant),
    repo: Repository = Depends(get_repo),
    runner: CodeRunner = Depends(get_runner),
) -> dict:
    files = {f["path"]: f["content"] for f in repo.list_files(session_id)}
    result = runner.run(session_id, body.entry_path, files)
    return repo.add_run(session_id, result.as_dict(), who.label, client_id)


@router.get("/api/sessions/{session_id}/changes", response_model=ChangesOut, tags=["shared"])
def changes(
    session_id: str,
    since: Annotated[int, Query(ge=-1, description="Last event id seen; -1 returns only the current cursor")] = 0,
    _: Participant = Depends(participant),
    repo: Repository = Depends(get_repo),
) -> ChangesOut:
    cursor, events = repo.changes(session_id, since)
    return ChangesOut(cursor=cursor, events=events)
