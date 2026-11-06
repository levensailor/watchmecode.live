"""Organizations: members, API keys, integration connections, and assessment templates."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app.auth import require_user
from app.crypto import new_api_key, new_webhook_secret
from app.integrations import ashby, modernloop
from app.integrations.http import IntegrationError
from app.languages import LANGUAGES
from app.repository import Repository
from app.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    AshbyConnect,
    IntegrationOut,
    MemberIn,
    MemberOut,
    ModernLoopConnect,
    OrgCreate,
    OrgDetail,
    OrgOut,
    SyncResult,
    TemplateIn,
    TemplateOut,
    WebhookSecretOut,
)
from app.urls import ashby_partner_base_url, ashby_webhook_url, base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orgs", tags=["organizations"])
PROVIDERS = ("ashby", "modernloop")


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


class OrgContext:
    def __init__(self, org: dict[str, Any], role: str, user: dict) -> None:
        self.org = org
        self.role = role
        self.user = user

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def org_member(org_id: str, user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> OrgContext:
    org = repo.get_org(org_id)
    role = repo.member_role(org_id, user["email"]) if org else None
    if not org or not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return OrgContext(org, role, user)


def org_admin(ctx: OrgContext = Depends(org_member)) -> OrgContext:
    if not ctx.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organization admins only")
    return ctx


def integration_out(request: Request, org: dict[str, Any], provider: str, row: dict[str, Any] | None) -> IntegrationOut:
    base = base_url(request.app.state.settings, request)
    extra: dict[str, Any] = {}
    if provider == "ashby":
        extra = {"webhook_url": ashby_webhook_url(base, org["slug"]), "partner_base_url": ashby_partner_base_url(base)}
    if not row:
        return IntegrationOut(provider=provider, connected=False, **extra)
    return IntegrationOut(
        provider=provider,
        connected=True,
        enabled=row["enabled"],
        settings=row["settings"],
        last_event_at=row["last_event_at"],
        last_synced_at=row["last_synced_at"],
        last_error=row["last_error"],
        **extra,
    )


# Organizations -------------------------------------------------------------------------


@router.get("", response_model=list[OrgOut])
def list_orgs(user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_orgs_for(user["email"])


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(body: OrgCreate, user: dict = Depends(require_user), repo: Repository = Depends(get_repo)) -> dict:
    org = repo.create_org(body.name.strip(), user["email"])
    return {**org, "role": "admin"}


@router.get("/{org_id}", response_model=OrgDetail)
def get_org(request: Request, ctx: OrgContext = Depends(org_member), repo: Repository = Depends(get_repo)) -> OrgDetail:
    org = ctx.org
    rows = {row["provider"]: row for row in repo.list_integrations(org["id"])}
    return OrgDetail(
        **org,
        role=ctx.role,
        members=repo.list_members(org["id"]),
        api_keys=repo.list_api_keys(org["id"]) if ctx.is_admin else [],
        integrations=[integration_out(request, org, provider, rows.get(provider)) for provider in PROVIDERS],
    )


# Members ---------------------------------------------------------------------------------


@router.post("/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(body: MemberIn, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> dict:
    email = body.email.strip().lower()
    if email == ctx.user["email"].lower() and body.role != "admin" and repo.admin_count(ctx.org["id"]) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "An organization needs at least one admin")
    return repo.upsert_member(ctx.org["id"], email, body.role)


@router.delete("/{org_id}/members/{email}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(email: str, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> None:
    role = repo.member_role(ctx.org["id"], email)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if role == "admin" and repo.admin_count(ctx.org["id"]) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "An organization needs at least one admin")
    repo.remove_member(ctx.org["id"], email)


# API keys ----------------------------------------------------------------------------------


@router.post("/{org_id}/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(body: ApiKeyCreate, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> dict:
    plaintext, prefix, key_hash = new_api_key()
    row = repo.create_api_key(ctx.org["id"], body.name.strip(), prefix, key_hash, ctx.user["email"])
    logger.info("API key %s created for org %s", row["id"], ctx.org["id"])
    return {**row, "key": plaintext}


@router.get("/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_api_keys(ctx.org["id"])


@router.delete("/{org_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(key_id: str, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> None:
    if not repo.revoke_api_key(ctx.org["id"], key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")


# Integrations ------------------------------------------------------------------------------


@router.put("/{org_id}/integrations/ashby", response_model=IntegrationOut)
def connect_ashby(
    body: AshbyConnect, request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)
) -> IntegrationOut:
    vault = request.app.state.vault
    existing = repo.get_integration(ctx.org["id"], "ashby")
    if body.api_key:
        credentials = vault.encrypt(body.api_key.strip())
    elif existing:
        credentials = existing["credentials"]
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "api_key is required to connect Ashby")
    secret = existing["webhook_secret"] if existing and existing["webhook_secret"] and not body.rotate_webhook_secret else None
    if secret is None:
        secret = vault.encrypt(new_webhook_secret())
    row = repo.upsert_integration(ctx.org["id"], "ashby", credentials, secret, body.settings.model_dump(mode="json"))
    logger.info("Ashby connected for org %s", ctx.org["id"])
    return integration_out(request, ctx.org, "ashby", row)


@router.get("/{org_id}/integrations/ashby/webhook-secret", response_model=WebhookSecretOut)
def ashby_webhook_secret(request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> WebhookSecretOut:
    row = repo.get_integration(ctx.org["id"], "ashby")
    if not row or not row["webhook_secret"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ashby is not connected")
    base = base_url(request.app.state.settings, request)
    return WebhookSecretOut(
        webhook_url=ashby_webhook_url(base, ctx.org["slug"]),
        webhook_secret=request.app.state.vault.decrypt(row["webhook_secret"]),
    )


@router.post("/{org_id}/integrations/ashby/test")
async def test_ashby(request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> dict:
    row = repo.get_integration(ctx.org["id"], "ashby")
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ashby is not connected")

    def check() -> dict:
        with ashby.client_for(request.app, row) as client:
            info = client.api_key_info()
        return {"ok": True, "detail": f"Connected as API key {info.get('title') or info.get('id', '')}".strip()}

    try:
        return await run_in_threadpool(check)
    except IntegrationError as exc:
        repo.mark_integration(ctx.org["id"], "ashby", last_error=str(exc)[:500])
        return {"ok": False, "detail": str(exc)}


@router.put("/{org_id}/integrations/modernloop", response_model=IntegrationOut)
def connect_modernloop(
    body: ModernLoopConnect, request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)
) -> IntegrationOut:
    existing = repo.get_integration(ctx.org["id"], "modernloop")
    if body.api_token:
        credentials = request.app.state.vault.encrypt(body.api_token.strip())
    elif existing:
        credentials = existing["credentials"]
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "api_token is required to connect ModernLoop")
    row = repo.upsert_integration(ctx.org["id"], "modernloop", credentials, None, body.settings.model_dump(mode="json"))
    logger.info("ModernLoop connected for org %s", ctx.org["id"])
    return integration_out(request, ctx.org, "modernloop", row)


@router.post("/{org_id}/integrations/modernloop/test")
async def test_modernloop(request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> dict:
    row = repo.get_integration(ctx.org["id"], "modernloop")
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModernLoop is not connected")

    def check() -> dict:
        with modernloop.client_for(request.app, row) as client:
            me = client.me()
        org_name = (me.get("organization") or {}).get("name", "")
        return {"ok": True, "detail": f"Connected to {org_name} as {me.get('email', 'API token')}".strip()}

    try:
        return await run_in_threadpool(check)
    except IntegrationError as exc:
        repo.mark_integration(ctx.org["id"], "modernloop", last_error=str(exc)[:500])
        return {"ok": False, "detail": str(exc)}


@router.post("/{org_id}/integrations/modernloop/sync", response_model=SyncResult)
async def sync_modernloop(request: Request, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> dict:
    row = repo.get_integration(ctx.org["id"], "modernloop")
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ModernLoop is not connected")
    base = base_url(request.app.state.settings, request)
    return await run_in_threadpool(modernloop.poll_org, request.app, ctx.org, row, base)


@router.delete("/{org_id}/integrations/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(provider: str, ctx: OrgContext = Depends(org_admin), repo: Repository = Depends(get_repo)) -> None:
    if provider not in PROVIDERS or not repo.delete_integration(ctx.org["id"], provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    logger.info("%s disconnected for org %s", provider, ctx.org["id"])


# Assessment templates -------------------------------------------------------------------------


@router.get("/{org_id}/assessments", response_model=list[TemplateOut])
def list_templates(ctx: OrgContext = Depends(org_member), repo: Repository = Depends(get_repo)) -> list[dict]:
    return repo.list_templates(ctx.org["id"])


def _template_files(body: TemplateIn) -> dict[str, str]:
    return body.files if body.files else dict(LANGUAGES[body.language].starter)


@router.post("/{org_id}/assessments", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: TemplateIn, ctx: OrgContext = Depends(org_member), repo: Repository = Depends(get_repo)) -> dict:
    return repo.save_template(
        ctx.org["id"], body.name.strip(), body.description, body.language.value, _template_files(body),
        body.time_limit_minutes, ctx.user["email"],
    )


@router.put("/{org_id}/assessments/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str, body: TemplateIn, ctx: OrgContext = Depends(org_member), repo: Repository = Depends(get_repo)
) -> dict:
    if not repo.get_template(ctx.org["id"], template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    return repo.save_template(
        ctx.org["id"], body.name.strip(), body.description, body.language.value, _template_files(body),
        body.time_limit_minutes, ctx.user["email"], template_id,
    )


@router.delete("/{org_id}/assessments/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, ctx: OrgContext = Depends(org_member), repo: Repository = Depends(get_repo)) -> None:
    if not repo.delete_template(ctx.org["id"], template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
