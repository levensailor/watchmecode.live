"""ModernLoop integration: poll the Platform API (no webhooks) and auto-create sessions."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app import plans
from app.auth import issue_join_token
from app.integrations.http import IntegrationError, make_client
from app.languages import LANGUAGES, LanguageId
from app.repository import PinExhaustedError, Repository, utcnow
from app.urls import base_url, join_url

logger = logging.getLogger(__name__)

PROVIDER = "modernloop"
PAGE_SIZE = 100
MAX_PAGES = 20
INITIAL_LOOKBACK = timedelta(days=1)
STALE_EVENT_GRACE = timedelta(hours=1)

router = APIRouter(tags=["integrations"])


class ModernLoopClient:
    def __init__(self, app: FastAPI, api_token: str) -> None:
        settings = app.state.settings
        self._client = make_client(
            settings,
            getattr(app.state, "http_transport", None),
            base_url=settings.modernloop_api_base,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
        )

    def __enter__(self) -> ModernLoopClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"ModernLoop {path} request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise IntegrationError(f"ModernLoop rejected the API token (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise IntegrationError(f"ModernLoop {path} returned HTTP {response.status_code}")
        return response.json()

    def me(self) -> dict[str, Any]:
        return self.get("/v1/me")

    def interview_events(self, updated_since: str) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"updated_at_gte": updated_since, "limit": PAGE_SIZE}
        for _ in range(MAX_PAGES):
            page = self.get("/v1/interview-events", params)
            yield from page.get("data") or []
            cursor = (page.get("pagination") or {}).get("next_cursor")
            if not cursor:
                return
            params = {**params, "cursor": cursor}

    def candidate(self, candidate_id: str) -> dict[str, Any]:
        return self.get(f"/v1/candidates/{candidate_id}")

    def user(self, user_id: str) -> dict[str, Any]:
        return self.get(f"/v1/users/{user_id}")


def client_for(app: FastAPI, integration: dict[str, Any]) -> ModernLoopClient:
    return ModernLoopClient(app, app.state.vault.decrypt(integration["credentials"]))


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def poll_org(app: FastAPI, org: dict[str, Any], integration: dict[str, Any], base: str) -> dict[str, Any]:
    repo: Repository = app.state.repo
    settings = integration["settings"]
    org_id = org["id"]
    result: dict[str, Any] = {"created": 0, "ended": 0, "skipped": 0, "errors": []}
    sync_started = utcnow()
    since = integration.get("last_synced_at") or (datetime.now(UTC) - INITIAL_LOOKBACK).isoformat(timespec="seconds")
    stage_ids = set(settings.get("job_stage_ids") or [])
    ashby = repo.get_integration(org_id, "ashby")
    ashby_active = bool(ashby and ashby["enabled"])
    language = LANGUAGES[LanguageId(settings.get("default_language", "python"))]
    now = datetime.now(UTC)
    candidates: dict[str, dict[str, Any]] = {}

    try:
        with client_for(app, integration) as client:
            for event in client.interview_events(since):
                event_id = event.get("id")
                if not event_id or event.get("is_debrief"):
                    result["skipped"] += 1
                    continue
                link = repo.get_link(org_id, PROVIDER, "interview_event", event_id)
                if event.get("status") == "CANCELED":
                    if link and _end(repo, link):
                        result["ended"] += 1
                    continue
                start = _parse(event.get("start_at"))
                application = event.get("application") or {}
                stage = (application.get("stage") or {}).get("id")
                if (
                    link
                    or event.get("status") != "SCHEDULED"
                    or (start and start < now - STALE_EVENT_GRACE)
                    or (stage_ids and stage not in stage_ids)
                ):
                    result["skipped"] += 1
                    continue

                candidate_ref = application.get("candidate") or {}
                candidate_id = candidate_ref.get("id")
                candidate = {}
                if candidate_id:
                    if candidate_id not in candidates:
                        candidates[candidate_id] = client.candidate(candidate_id)
                    candidate = candidates[candidate_id]
                ats = candidate.get("ats") or {}
                if settings.get("skip_ashby_candidates", True) and ashby_active and ats.get("type") == "ASHBY":
                    result["skipped"] += 1
                    continue

                emails: list[str] = []
                for interviewer in event.get("interviewers") or []:
                    try:
                        email = client.user(interviewer["id"]).get("email")
                    except (IntegrationError, KeyError):
                        email = None
                    if email:
                        emails.append(email)
                name = (candidate.get("name") or {}).get("full") or candidate_ref.get("name") or "Candidate"
                try:
                    session = repo.create_session(
                        name,
                        emails[0] if emails else org["created_by"],
                        language.id.value,
                        dict(language.starter),
                        org_id=org_id,
                        mode="live",
                        title=event.get("name") or "ModernLoop interview",
                        candidate_email=candidate.get("primary_email"),
                    )
                except PinExhaustedError as exc:
                    result["errors"].append(str(exc))
                    continue
                link_url = join_url(base, issue_join_token(app.state.settings, session["id"]))
                repo.add_link(
                    org_id,
                    session["id"],
                    PROVIDER,
                    "interview_event",
                    event_id,
                    parent_id=event.get("schedule_id"),
                    application_id=application.get("id"),
                    candidate_id=candidate_id,
                    data={"start_time": event.get("start_at"), "join_url": link_url, "app_url": event.get("app_url")},
                )
                for email in emails:
                    if repo.member_role(org_id, email) is None:
                        repo.upsert_member(org_id, email, "interviewer")
                result["created"] += 1
    except IntegrationError as exc:
        result["errors"].append(str(exc))

    if result["errors"]:
        repo.mark_integration(org_id, PROVIDER, last_error="; ".join(result["errors"])[:500])
    else:
        repo.mark_integration(org_id, PROVIDER, last_synced_at=sync_started, last_error=None)
    logger.info("ModernLoop sync for org %s: %s", org_id, result)
    return result


def _end(repo: Repository, link: dict[str, Any]) -> bool:
    session = repo.get_session(link["session_id"])
    if not session or session["status"] != "active":
        return False
    repo.set_status(session["id"], "ended", "ModernLoop (cancelled)")
    repo.update_link_data(link["id"], {**link["data"], "cancelled": "interview cancelled"})
    return True


def poll_all(app: FastAPI, base: str | None = None) -> dict[str, Any]:
    """Poll every connected org in every workspace that has the integrations feature."""
    registry = app.state.tenants
    settings = app.state.settings
    totals = {"tenants": 0, "orgs": 0, "created": 0, "ended": 0, "skipped": 0, "errors": 0}
    for tenant in registry.active_tenants():
        if not plans.has_feature(settings, tenant, "integrations"):
            continue
        with registry.use(tenant) as repo:
            totals["tenants"] += 1
            tenant_base = base_url(settings) if tenant is not None else (base or base_url(settings))
            for integration in repo.list_enabled_integrations(PROVIDER):
                org = repo.get_org(integration["org_id"])
                if not org:
                    continue
                result = poll_org(app, org, integration, tenant_base)
                totals["orgs"] += 1
                for key in ("created", "ended", "skipped"):
                    totals[key] += result[key]
                totals["errors"] += len(result["errors"])
    return totals


@router.get("/api/cron/modernloop", summary="Vercel cron: poll ModernLoop for every connected org")
async def cron_modernloop(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.cron_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CRON_SECRET is not configured")
    expected = f"Bearer {settings.cron_secret}"
    if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return await run_in_threadpool(poll_all, request.app, base_url(settings, request))
