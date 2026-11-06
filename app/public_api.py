"""Public REST API (/v1) for schedulers and ATS partners, authenticated with org API keys."""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app import plans
from app.auth import issue_join_token
from app.crypto import hash_api_key
from app.integrations import hooks
from app.languages import LANGUAGES
from app.repository import PinExhaustedError, Repository
from app.results import build_summary, build_zip, zip_filename
from app.schemas import PadCreate, PadOut, ResultSummary
from app.tenancy import current_tenant
from app.urls import base_url, interviewer_url, join_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["public api"], dependencies=[Depends(plans.require_feature("api"))])
DEFAULT_TAKE_HOME_MINUTES = 60


def api_key_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    if scheme.lower() == "basic" and value.strip():
        try:
            return base64.b64decode(value.strip()).decode().split(":", 1)[0] or None
        except (binascii.Error, UnicodeDecodeError):
            return None
    return None


def api_org(request: Request) -> dict[str, Any]:
    key = api_key_from(request)
    org = request.app.state.repo.org_for_api_key_hash(hash_api_key(key)) if key else None
    if not org:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key", headers={"WWW-Authenticate": 'Bearer realm="watchmecode"'}
        )
    return org


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def _pad(request: Request, session: dict[str, Any], include_results: bool = False) -> PadOut:
    repo: Repository = request.app.state.repo
    base = base_url(request.app.state.settings, request)
    results = build_summary(repo, base, session) if include_results else None
    return PadOut(
        id=session["id"],
        pin=session["pin"],
        candidate_name=session["candidate_name"],
        title=session.get("title"),
        language=session["language"],
        mode=session.get("mode") or "live",
        status=session["status"],
        join_url=join_url(base, issue_join_token(request.app.state.settings, session["id"])),
        interviewer_url=interviewer_url(base, session["id"]),
        created_at=session["created_at"],
        results=results,
    )


def _org_session(repo: Repository, org: dict[str, Any], pad_id: str) -> dict[str, Any]:
    session = repo.get_session(pad_id)
    if not session or session.get("org_id") != org["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pad not found")
    return session


@router.post("/pads", response_model=PadOut, status_code=status.HTTP_201_CREATED, summary="Create a coding pad")
def create_pad(body: PadCreate, request: Request, org: dict = Depends(api_org), repo: Repository = Depends(get_repo)) -> PadOut:
    language = LANGUAGES[body.language]
    settings = request.app.state.settings
    plans.require_active(request)
    plans.check_language(settings, language.id.value)
    if body.mode == "take_home":
        plans.check_take_home_quota(settings, request.app.state.tenants, current_tenant.get())
    minutes = body.time_limit_minutes or (DEFAULT_TAKE_HOME_MINUTES if body.mode == "take_home" else None)
    owner = (body.interviewer_email or org["key_created_by"] or org["created_by"]).lower()
    try:
        session = repo.create_session(
            body.candidate_name.strip(),
            owner,
            language.id.value,
            body.files or dict(language.starter),
            org_id=org["id"],
            mode=body.mode,
            title=body.title,
            candidate_email=body.candidate_email,
            time_limit_minutes=minutes,
        )
    except PinExhaustedError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if body.interviewer_email and repo.member_role(org["id"], owner) is None:
        repo.upsert_member(org["id"], owner, "interviewer")
    logger.info("API created pad %s for org %s", session["id"], org["id"])
    return _pad(request, session)


@router.get("/pads", response_model=list[PadOut], summary="List recent pads")
def list_pads(
    request: Request,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(active|ended)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    org: dict = Depends(api_org),
    repo: Repository = Depends(get_repo),
) -> list[PadOut]:
    sessions = [s for s in repo.list_org_sessions(org["id"], limit) if not status_filter or s["status"] == status_filter]
    return [_pad(request, s) for s in sessions]


@router.get("/pads/{pad_id}", response_model=PadOut, summary="Get a pad, with results once it has ended")
def get_pad(pad_id: str, request: Request, org: dict = Depends(api_org), repo: Repository = Depends(get_repo)) -> PadOut:
    session = _org_session(repo, org, pad_id)
    return _pad(request, session, include_results=session["status"] == "ended")


@router.post("/pads/{pad_id}/end", response_model=PadOut, summary="End a pad and push results to integrations")
def end_pad(pad_id: str, request: Request, org: dict = Depends(api_org), repo: Repository = Depends(get_repo)) -> PadOut:
    session = _org_session(repo, org, pad_id)
    if session["status"] == "active":
        session = repo.set_status(pad_id, "ended", "API")
        hooks.session_finished(request.app, pad_id, None, base_url(request.app.state.settings, request))
    return _pad(request, session, include_results=True)


@router.get("/pads/{pad_id}/results", response_model=ResultSummary, summary="Results summary")
def pad_results(pad_id: str, request: Request, org: dict = Depends(api_org), repo: Repository = Depends(get_repo)) -> dict:
    session = _org_session(repo, org, pad_id)
    return build_summary(repo, base_url(request.app.state.settings, request), session)


@router.get("/pads/{pad_id}/files.zip", response_class=Response, summary="Final code as a zip")
def pad_files(pad_id: str, org: dict = Depends(api_org), repo: Repository = Depends(get_repo)) -> Response:
    session = _org_session(repo, org, pad_id)
    return Response(
        build_zip(repo, pad_id),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename(session)}"'},
    )
