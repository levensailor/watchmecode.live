"""Ashby integration: signed webhooks, Assessments Partner API, and results write-back."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.auth import issue_join_token
from app.crypto import hash_api_key, verify_signature
from app.integrations.ashby_client import AshbyClient
from app.integrations.email import send_email, take_home_invite
from app.integrations.http import IntegrationError
from app.languages import LANGUAGES, LanguageId
from app.repository import PinExhaustedError, Repository, utcnow
from app.results import build_summary, build_zip, render_link_note, render_note, zip_filename
from app.urls import base_url, interviewer_url, join_url

logger = logging.getLogger(__name__)

PROVIDER = "ashby"
SCHEDULE_ACTIONS = {"interviewScheduleCreate", "interviewScheduleUpdate"}
LIVE_ASSESSMENT_TYPE = "watchmecode-live-interview"
DEFAULT_TAKE_HOME_MINUTES = 60

router = APIRouter(tags=["integrations"])
partner_router = APIRouter(prefix="/partners/ashby", tags=["ashby partner"])


# Helpers ---------------------------------------------------------------------------


def client_for(app: FastAPI, integration: dict[str, Any]) -> AshbyClient:
    api_key = app.state.vault.decrypt(integration["credentials"])
    return AshbyClient(app.state.settings, api_key, getattr(app.state, "http_transport", None))


def typed(identifier: str, label: str, value_type: str, value: Any, description: str | None = None) -> dict[str, Any]:
    field = {"identifier": identifier, "label": label, "type": value_type, "value": value}
    if description:
        field["description"] = description
    return field


def now_ms() -> int:
    return int(time.time() * 1000)


def _record_error(repo: Repository, org_id: str, message: str) -> None:
    logger.warning("Ashby integration error for org %s: %s", org_id, message)
    repo.mark_integration(org_id, PROVIDER, last_error=message[:500])


def _add_interviewers_as_members(repo: Repository, org_id: str, emails: list[str]) -> None:
    for email in emails:
        if repo.member_role(org_id, email) is None:
            repo.upsert_member(org_id, email, "interviewer")


def end_linked_session(app: FastAPI, link: dict[str, Any], reason: str) -> bool:
    repo: Repository = app.state.repo
    session = repo.get_session(link["session_id"])
    if not session or session["status"] != "active":
        return False
    repo.set_status(session["id"], "ended", f"Ashby ({reason})")
    repo.update_link_data(link["id"], {**link["data"], "cancelled": reason, "results_pushed_at": utcnow()})
    return True


# Webhooks --------------------------------------------------------------------------


def process_schedule(
    app: FastAPI, org: dict[str, Any], integration: dict[str, Any], schedule: dict[str, Any], base: str
) -> dict[str, int]:
    repo: Repository = app.state.repo
    settings = integration["settings"]
    org_id = org["id"]
    schedule_id = schedule.get("id")
    events = schedule.get("interviewEvents") or []
    counts = {"created": 0, "ended": 0, "skipped": 0}
    if not schedule_id:
        return counts

    existing = {link["external_id"]: link for link in repo.links_for_parent(org_id, PROVIDER, schedule_id)}
    if schedule.get("status") == "Cancelled":
        counts["ended"] = sum(end_linked_session(app, link, "schedule cancelled") for link in existing.values())
        return counts

    current_ids = {event.get("id") for event in events}
    for event_id, link in existing.items():
        if event_id not in current_ids and end_linked_session(app, link, "interview removed"):
            counts["ended"] += 1

    stage_ids = settings.get("interview_stage_ids") or []
    if stage_ids and schedule.get("interviewStageId") not in stage_ids:
        counts["skipped"] = len(events)
        return counts
    if schedule.get("status") != "Scheduled":
        counts["skipped"] = len(events)
        return counts

    new_events = [event for event in events if event.get("id") and event["id"] not in existing]
    if not new_events:
        return counts

    with client_for(app, integration) as client:
        try:
            application = client.application_info(schedule["applicationId"])
        except (IntegrationError, KeyError) as exc:
            _record_error(repo, org_id, f"Could not load application: {exc}")
            counts["skipped"] = len(new_events)
            return counts
        candidate = application.get("candidate") or {}
        job_title = (application.get("job") or {}).get("title")
        email = (candidate.get("primaryEmailAddress") or {}).get("value")
        language = LANGUAGES[LanguageId(settings.get("default_language", "python"))]

        for event in new_events:
            interviewers = [
                {"id": i.get("id"), "email": i.get("email"), "name": f"{i.get('firstName', '')} {i.get('lastName', '')}".strip()}
                for i in event.get("interviewers") or []
            ]
            emails = [i["email"] for i in interviewers if i["email"]]
            owner = emails[0] if emails else org["created_by"]
            title = f"{job_title} interview" if job_title else "Ashby interview"
            try:
                session = repo.create_session(
                    candidate.get("name") or "Candidate",
                    owner,
                    language.id.value,
                    dict(language.starter),
                    org_id=org_id,
                    mode="live",
                    title=title,
                    candidate_email=email,
                )
            except PinExhaustedError as exc:
                _record_error(repo, org_id, str(exc))
                continue
            link_url = join_url(base, issue_join_token(app.state.settings, session["id"]))
            repo.add_link(
                org_id,
                session["id"],
                PROVIDER,
                "interview_event",
                event["id"],
                parent_id=schedule_id,
                application_id=schedule.get("applicationId"),
                candidate_id=candidate.get("id"),
                data={"interviewers": interviewers, "start_time": event.get("startTime"), "join_url": link_url},
            )
            _add_interviewers_as_members(repo, org_id, emails)
            counts["created"] += 1
            logger.info("Ashby event %s -> session %s", event["id"], session["id"])

            if candidate.get("id") and settings.get("candidate_link_field_id"):
                try:
                    client.set_candidate_field(candidate["id"], settings["candidate_link_field_id"], link_url)
                except IntegrationError as exc:
                    _record_error(repo, org_id, str(exc))
            if candidate.get("id") and settings.get("post_link_note", True):
                note = render_link_note(
                    app.state.settings.app_name, link_url, session["pin"], interviewer_url(base, session["id"]), event.get("startTime")
                )
                try:
                    client.create_note(candidate["id"], note)
                except IntegrationError as exc:
                    _record_error(repo, org_id, str(exc))
    return counts


@router.post("/integrations/ashby/webhook/{org_slug}", summary="Ashby webhook receiver (HMAC signed)")
async def ashby_webhook(org_slug: str, request: Request) -> dict[str, Any]:
    app = request.app
    repo: Repository = app.state.repo
    org = repo.get_org_by_slug(org_slug)
    integration = repo.get_integration(org["id"], PROVIDER) if org else None
    if not org or not integration or not integration["enabled"] or not integration["webhook_secret"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown webhook")

    raw = await request.body()
    secret = app.state.vault.decrypt(integration["webhook_secret"])
    if not verify_signature(secret, raw, request.headers.get("Ashby-Signature")):
        logger.warning("Rejected Ashby webhook with a bad signature for org %s", org["id"])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be JSON") from exc

    action = payload.get("action")
    repo.mark_integration(org["id"], PROVIDER, last_event_at=utcnow())
    if action not in SCHEDULE_ACTIONS:
        return {"ok": True, "ignored": action}
    schedule = (payload.get("data") or {}).get("interviewSchedule") or {}
    counts = await run_in_threadpool(process_schedule, app, org, integration, schedule, base_url(app.state.settings, request))
    return {"ok": True, "action": action, **counts}


# Results write-back -------------------------------------------------------------------


def push_results(app: FastAPI, session: dict[str, Any], link: dict[str, Any], actor_email: str | None, base: str) -> list[str]:
    repo: Repository = app.state.repo
    integration = repo.get_integration(link["org_id"], PROVIDER)
    if not integration or not integration["enabled"] or link["data"].get("results_pushed_at"):
        return []
    settings = integration["settings"]
    summary = build_summary(repo, base, session)
    errors: list[str] = []
    candidate_id = link.get("candidate_id")

    with client_for(app, integration) as client:
        on_behalf = client.find_user_id(actor_email) if actor_email else None

        def attempt(label: str, func, *args, **kwargs) -> None:
            try:
                func(*args, **kwargs)
            except IntegrationError as exc:
                errors.append(f"{label}: {exc}")

        if link["external_type"] == "assessment":
            attempt("assessment.update", client.assessment_update, take_home_result(link["external_id"], summary))
        else:
            if candidate_id and settings.get("push_results_note", True):
                attempt("candidate.createNote", client.create_note, candidate_id, render_note(app.state.settings.app_name, summary), on_behalf)
            if candidate_id and settings.get("add_completed_assessment"):
                attempt("assessment.addCompletedToCandidate", client.add_completed_assessment, completed_live_assessment(app, candidate_id, summary))
        if candidate_id and settings.get("upload_files", True) and summary["file_count"]:
            attempt("candidate.uploadFile", client.upload_file, candidate_id, zip_filename(session), build_zip(repo, session["id"]))

    repo.update_link_data(link["id"], {**link["data"], "results_pushed_at": utcnow(), "results_errors": errors})
    if errors:
        _record_error(repo, link["org_id"], "; ".join(errors))
    logger.info("Pushed results for session %s to Ashby (%d errors)", session["id"], len(errors))
    return errors


def push_recording_note(app: FastAPI, session: dict[str, Any], link: dict[str, Any], playback: str, actor_email: str | None) -> None:
    repo: Repository = app.state.repo
    integration = repo.get_integration(link["org_id"], PROVIDER)
    candidate_id = link.get("candidate_id")
    if not integration or not integration["enabled"] or not candidate_id:
        return
    if not integration["settings"].get("push_results_note", True):
        return
    note = f'<p><strong>Interview recording saved</strong></p><p><a href="{playback}">Watch playback</a></p>'
    with client_for(app, integration) as client:
        try:
            client.create_note(candidate_id, note, client.find_user_id(actor_email) if actor_email else None)
        except IntegrationError as exc:
            _record_error(repo, link["org_id"], str(exc))


def completed_live_assessment(app: FastAPI, candidate_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    metadata = [
        typed("runs", "Runs", "numeric_score", summary["run_count"]),
        typed("language", "Language", "string", summary["language"]),
        typed("session", "Session", "string", summary["interviewer_url"]),
    ]
    if summary["duration_minutes"] is not None:
        metadata.append(typed("duration", "Duration", "numeric_duration_minutes", summary["duration_minutes"]))
    if summary["playback_url"]:
        metadata.append(typed("playback", "Playback", "string", summary["playback_url"]))
    return {
        "candidateId": candidate_id,
        "partnerId": app.state.settings.ashby_partner_id,
        "assessment": {
            "assessmentTypeId": LIVE_ASSESSMENT_TYPE,
            "assessmentId": summary["session_id"],
            "assessmentName": summary["title"] or "watchmecode live interview",
            "result": typed("result", "Result", "string", "Completed"),
            "metadata": metadata,
        },
        "timestamp": now_ms(),
    }


def take_home_result(assessment_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    exit_text = f", last exit {summary['last_run_exit_code']}" if summary["last_run_exit_code"] is not None else ""
    metadata = [
        typed("runs", "Runs", "numeric_score", summary["run_count"]),
        typed("files", "Files", "numeric_score", summary["file_count"]),
        typed("download", "Final code (zip)", "url", summary["files_url"]),
    ]
    if summary["duration_minutes"] is not None:
        metadata.append(typed("duration", "Time spent", "numeric_duration_minutes", summary["duration_minutes"]))
    if summary["playback_url"]:
        metadata.append(typed("playback", "Playback", "url", summary["playback_url"]))
    return {
        "assessment_id": assessment_id,
        "timestamp": now_ms(),
        "assessment_status": typed("status", "Status", "string", "Submitted"),
        "assessment_result": typed("result", "Result", "string", f"Submitted · {summary['run_count']} runs{exit_text}"),
        "assessment_profile_url": typed("profile", "View in watchmecode", "url", summary["interviewer_url"]),
        "metadata": metadata,
    }


def report_take_home_started(app: FastAPI, link: dict[str, Any]) -> None:
    repo: Repository = app.state.repo
    integration = repo.get_integration(link["org_id"], PROVIDER)
    if not integration or not integration["enabled"]:
        return
    with client_for(app, integration) as client:
        try:
            client.assessment_update(
                {
                    "assessment_id": link["external_id"],
                    "timestamp": now_ms(),
                    "assessment_status": typed("status", "Status", "string", "Started"),
                }
            )
        except IntegrationError as exc:
            _record_error(repo, link["org_id"], str(exc))


# Assessments Partner API (Ashby calls these) ----------------------------------------


class PartnerCandidate(BaseModel):
    ashby_id: str
    first_name: str = ""
    last_name: str = ""
    email: str | None = None
    ashby_profile_url: str | None = None


class PartnerApplication(BaseModel):
    ashby_id: str
    status: str | None = None


class PartnerHiringMember(BaseModel):
    email: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    role: str | None = None
    userId: str | None = None


class PartnerJob(BaseModel):
    ashby_id: str
    name: str = ""
    req_id: str | None = None
    ashby_job_url: str | None = None
    hiringTeam: list[PartnerHiringMember] = Field(default_factory=list)


class AssessmentStartIn(BaseModel):
    assessment_type_id: str
    candidate: PartnerCandidate
    application: PartnerApplication
    job: PartnerJob


class AssessmentCancelIn(BaseModel):
    assessment_id: str


def partner_error(code: str, message: str, http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"success": False, "errors": [code], "errorInfo": {"code": code, "message": message}}, status_code=http_status
    )


def partner_org(request: Request) -> dict[str, Any]:
    """Ashby sends the watchmecode API key as the Basic Auth username."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    key = ""
    if scheme.lower() == "basic" and value:
        try:
            key = base64.b64decode(value.strip()).decode().split(":", 1)[0]
        except (binascii.Error, UnicodeDecodeError):
            key = ""
    elif scheme.lower() == "bearer":
        key = value.strip()
    org = request.app.state.repo.org_for_api_key_hash(hash_api_key(key)) if key else None
    if not org:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid watchmecode API key", headers={"WWW-Authenticate": "Basic"})
    return org


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@partner_router.post("/assessment.list", summary="List assessment types (called by Ashby)")
async def assessment_list(request: Request) -> dict[str, Any]:
    org = partner_org(request)
    templates = request.app.state.repo.list_templates(org["id"])
    return {
        "success": True,
        "results": [
            {"assessment_type_id": t["id"], "name": t["name"], "description": t["description"] or None} for t in templates
        ],
    }


@partner_router.post("/customFields.fetch", summary="Custom fields synced to Ashby (none)")
async def custom_fields_fetch(request: Request) -> dict[str, Any]:
    partner_org(request)
    return {"success": True, "fields": []}


def start_assessment(app: FastAPI, org: dict[str, Any], body: AssessmentStartIn, base: str) -> dict[str, Any] | JSONResponse:
    repo: Repository = app.state.repo
    template = repo.get_template(org["id"], body.assessment_type_id)
    if not template:
        return partner_error("unknown_assessment_type", "That assessment no longer exists in watchmecode")

    for link in repo.links_for_parent(org["id"], PROVIDER, body.application.ashby_id):
        session = repo.get_session(link["session_id"])
        if (
            link["external_type"] == "assessment"
            and link["data"].get("template_id") == template["id"]
            and session
            and session["status"] == "active"
        ):
            return {"success": True, "results": {"assessment_id": link["external_id"], "update_request": _invited_update(link["external_id"], base, session, link["data"].get("join_url"))}}

    name = f"{body.candidate.first_name} {body.candidate.last_name}".strip() or "Candidate"
    owner = next((m.email for m in body.job.hiringTeam if m.email), None) or org["created_by"]
    minutes = template["time_limit_minutes"] or DEFAULT_TAKE_HOME_MINUTES
    try:
        session = repo.create_session(
            name,
            owner,
            template["language"],
            template["files"],
            org_id=org["id"],
            mode="take_home",
            title=template["name"],
            candidate_email=body.candidate.email,
            time_limit_minutes=minutes,
        )
    except PinExhaustedError:
        return partner_error("unavailable", "Could not allocate a session; try again")

    assessment_id = session["id"]
    link_url = join_url(base, issue_join_token(app.state.settings, session["id"]))
    repo.add_link(
        org["id"],
        session["id"],
        PROVIDER,
        "assessment",
        assessment_id,
        parent_id=body.application.ashby_id,
        application_id=body.application.ashby_id,
        candidate_id=body.candidate.ashby_id,
        data={
            "template_id": template["id"],
            "job": body.job.name,
            "ashby_profile_url": body.candidate.ashby_profile_url,
            "join_url": link_url,
        },
    )
    if body.candidate.email:
        subject, html_body = take_home_invite(app.state.settings.app_name, name, template["name"], link_url, minutes)
        send_email(app.state.settings, body.candidate.email, subject, html_body, getattr(app.state, "http_transport", None))
    logger.info("Ashby assessment started: session %s for org %s", session["id"], org["id"])
    return {"success": True, "results": {"assessment_id": assessment_id, "update_request": _invited_update(assessment_id, base, session, link_url)}}


def _invited_update(assessment_id: str, base: str, session: dict[str, Any], link_url: str | None) -> dict[str, Any]:
    update = {
        "assessment_id": assessment_id,
        "timestamp": now_ms(),
        "assessment_status": typed("status", "Status", "string", "Invited"),
        "assessment_profile_url": typed("profile", "View in watchmecode", "url", interviewer_url(base, session["id"])),
        "metadata": [],
    }
    if link_url:
        update["metadata"].append(typed("candidate_link", "Candidate link", "url", link_url))
    return update


@partner_router.post("/assessment.start", summary="Start a take-home assessment (called by Ashby)")
async def assessment_start(request: Request):
    org = partner_org(request)
    try:
        body = AssessmentStartIn.model_validate(await _json(request))
    except ValidationError as exc:
        return partner_error("invalid_request", exc.errors()[0].get("msg", "Invalid request"))
    return await run_in_threadpool(start_assessment, request.app, org, body, base_url(request.app.state.settings, request))


@partner_router.post("/assessment.cancel", summary="Cancel a take-home assessment (called by Ashby)")
async def assessment_cancel(request: Request):
    org = partner_org(request)
    try:
        body = AssessmentCancelIn.model_validate(await _json(request))
    except ValidationError:
        return partner_error("invalid_request", "assessment_id is required")
    repo: Repository = request.app.state.repo
    link = repo.get_link(org["id"], PROVIDER, "assessment", body.assessment_id)
    if not link:
        return partner_error("not_found", "Unknown assessment")
    end_linked_session(request.app, link, "cancelled in Ashby")
    return {
        "success": True,
        "results": {
            "assessment_id": body.assessment_id,
            "update_request": {
                "assessment_id": body.assessment_id,
                "timestamp": now_ms(),
                "cancelled_reason": typed("cancelled", "Cancelled", "string", "Cancelled in Ashby"),
            },
        },
    }
