"""Product-level API: the apex hub (signup, workspace picker, Stripe webhook) and per-workspace settings."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import billing, handoff, plans
from app.auth import handoff_redirect, require_user, safe_next, session_user
from app.integrations.http import IntegrationError
from app.schemas import EMAIL_PATTERN
from app.tenancy import apex_url, current_site, current_tenant, slug_error, tenant_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["platform"])
DOMAIN_PATTERN = re.compile(r"^(?=.{3,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80, description="Company or team name")
    slug: str = Field(min_length=2, max_length=31, description="Subdomain, e.g. 'ae' for ae.watchmecode.live")


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    allowed_domains: list[str] | None = Field(default=None, max_length=10)


class MemberIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    role: Literal["admin", "interviewer"] = "interviewer"


class CheckoutIn(BaseModel):
    plan: Literal["starter", "team"]
    interval: Literal["monthly", "annual"] = "annual"


def _registry(request: Request):
    registry = request.app.state.tenants
    if not registry.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not available in single-workspace mode")
    return registry


def _apex_only(request: Request):
    registry = _registry(request)
    if current_site.get() != "apex":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return registry


def _workspace(request: Request) -> dict[str, Any]:
    _registry(request)
    tenant = current_tenant.get()
    if tenant is None or current_site.get() != "tenant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return tenant


def workspace_admin(request: Request, user: dict = Depends(require_user)) -> tuple[dict[str, Any], dict]:
    tenant = _workspace(request)
    if request.app.state.tenants.role_for(tenant, user["email"]) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace admins only")
    return tenant, user


def _brief(settings, tenant: dict[str, Any], role: str | None = None) -> dict[str, Any]:
    plan = plans.plan_for(tenant)
    return {
        "slug": tenant["slug"],
        "name": tenant["name"],
        "url": tenant_url(settings, tenant),
        "plan": plan.id,
        "plan_name": plan.name,
        "state": plans.state_of(tenant),
        "trial_days_left": plans.trial_days_left(tenant),
        "role": role,
    }


def plan_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "monthly_price": p.monthly_price,
            "annual_price": p.annual_price,
            "credits": p.credits,
            "overage": p.overage,
            "take_homes": p.take_homes,
            "features": sorted(p.features),
            "blurb": p.blurb,
        }
        for p in plans.PLANS.values()
    ]


# Apex hub -----------------------------------------------------------------------------------


@router.get("/.well-known/watchmecode-handoff.json", include_in_schema=False)
def handoff_key(request: Request) -> dict[str, str]:
    return {"alg": "Ed25519", "public_key": handoff.public_key_b64(request.app.state.settings)}


@router.get("/api/platform/plans", summary="Plans and prices")
def list_plans() -> list[dict[str, Any]]:
    return plan_catalog()


@router.get("/api/platform/me", summary="Signed-in user on the hub and their workspaces")
def platform_me(request: Request) -> dict[str, Any]:
    registry = _apex_only(request)
    settings = request.app.state.settings
    user = session_user(request)
    workspaces = []
    if user:
        for tenant in registry.platform.tenants_for_email(user["email"]):
            if tenant["status"] != "deleted":
                workspaces.append(_brief(settings, tenant, tenant.get("role")))
        default = registry.default_tenant()
        if default and default["open_access"] and all(w["slug"] != default["slug"] for w in workspaces):
            workspaces.append(_brief(settings, default, "interviewer"))
    return {"user": user, "workspaces": workspaces, "google_enabled": settings.google_configured}


@router.get("/api/platform/subdomains/{slug}", summary="Check whether a subdomain is free")
def check_subdomain(slug: str, request: Request) -> dict[str, Any]:
    registry = _apex_only(request)
    slug = slug.strip().lower()
    reason = slug_error(request.app.state.settings, slug)
    if reason is None and registry.platform.get_tenant(slug):
        reason = "That subdomain is taken."
    return {"slug": slug, "available": reason is None, "reason": reason}


@router.post("/api/platform/workspaces", status_code=status.HTTP_201_CREATED, summary="Create a workspace (14-day trial)")
def create_workspace(body: WorkspaceCreate, request: Request) -> dict[str, Any]:
    registry = _apex_only(request)
    settings = request.app.state.settings
    user = session_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in with Google first")
    slug = body.slug.strip().lower()
    reason = slug_error(settings, slug)
    if reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, reason)
    if registry.platform.get_tenant(slug):
        raise HTTPException(status.HTTP_409_CONFLICT, "That subdomain is taken.")
    try:
        tenant = registry.provision(slug=slug, name=body.name.strip(), owner_email=user["email"])
    except Exception as exc:  # a concurrent signup can win the unique slug
        if registry.platform.get_tenant(slug):
            raise HTTPException(status.HTTP_409_CONFLICT, "That subdomain is taken.") from exc
        raise
    logger.info("Workspace %s created by %s", slug, user["email"])
    return {**_brief(settings, tenant, "owner"), "go_url": f"/api/platform/go/{slug}?next=/"}


@router.get("/api/platform/go/{slug}", summary="Sign in to a workspace through the hub")
def go_to_workspace(slug: str, request: Request, next: str | None = None) -> RedirectResponse:
    _apex_only(request)
    user = session_user(request)
    if not user:
        return RedirectResponse(f"/auth/google?tenant={slug}&next={safe_next(next)}", status_code=303)
    return handoff_redirect(request, user, slug, safe_next(next))


@router.post("/api/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request) -> dict[str, str]:
    _registry(request)
    settings = request.app.state.settings
    payload = await request.body()
    if not billing.verify_webhook(settings.stripe_webhook_secret, payload, request.headers.get("stripe-signature")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from exc
    outcome = await run_in_threadpool(billing.apply_event, request.app, event)
    logger.info("Stripe webhook %s %s: %s", event.get("type"), event.get("id"), outcome)
    return {"status": outcome}


# Workspace settings (on {slug}.watchmecode.live) ---------------------------------------


@router.get("/api/workspace", summary="This workspace: plan, usage, and members")
def get_workspace(request: Request, user: dict = Depends(require_user)) -> dict[str, Any]:
    return _workspace_payload(request, _workspace(request), user)


def _workspace_payload(request: Request, tenant: dict[str, Any], user: dict) -> dict[str, Any]:
    registry = request.app.state.tenants
    settings = request.app.state.settings
    role = registry.role_for(tenant, user["email"])
    admin = role in ("owner", "admin")
    return {
        **_brief(settings, tenant, role),
        "usage": plans.usage_summary(settings, registry, tenant),
        "allowed_domains": tenant["allowed_domains"],
        "members": registry.platform.list_members(tenant["id"]) if admin else [],
        "billing_enabled": settings.stripe_configured,
        "has_billing_account": bool(tenant.get("stripe_customer_id")),
        "billing_interval": tenant.get("billing_interval"),
        "plans": plan_catalog(),
        "switch_url": f"{apex_url(settings)}/login",
        "features": sorted(plans.plan_for(tenant).features),
    }


@router.put("/api/workspace", summary="Rename the workspace or set allowed sign-in domains")
def update_workspace(body: WorkspaceUpdate, request: Request, ctx=Depends(workspace_admin)) -> dict[str, Any]:
    tenant, _user = ctx
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if body.allowed_domains is not None:
        domains = sorted({d.strip().lower().lstrip("@") for d in body.allowed_domains if d.strip()})
        bad = [d for d in domains if not DOMAIN_PATTERN.match(d)]
        if bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not a domain: {', '.join(bad)}")
        fields["allowed_domains"] = domains
    registry = request.app.state.tenants
    if fields:
        registry.platform.update_tenant(tenant["id"], **fields)
        registry.invalidate(tenant["slug"])
    return _workspace_payload(request, registry.tenant(tenant["slug"]) or tenant, ctx[1])


@router.post("/api/workspace/members", status_code=status.HTTP_201_CREATED, summary="Add or change a member")
def add_member(body: MemberIn, request: Request, ctx=Depends(workspace_admin)) -> dict[str, Any]:
    tenant, _user = ctx
    registry = request.app.state.tenants
    existing = registry.platform.get_membership(tenant["id"], body.email)
    if existing and existing["role"] == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "The owner's role can't be changed")
    member = registry.platform.upsert_member(tenant["id"], body.email, body.role)
    registry.invalidate(tenant["slug"])
    return member


@router.delete("/api/workspace/members/{email}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a member")
def remove_member(email: str, request: Request, ctx=Depends(workspace_admin)) -> None:
    tenant, _user = ctx
    registry = request.app.state.tenants
    if not registry.platform.remove_member(tenant["id"], email):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found (owners can't be removed)")
    registry.invalidate(tenant["slug"])


@router.post("/api/workspace/checkout", summary="Start Stripe Checkout to choose a plan")
def start_checkout(body: CheckoutIn, request: Request, ctx=Depends(workspace_admin)) -> dict[str, str]:
    tenant, user = ctx
    try:
        return {"url": billing.checkout_url(request.app, tenant, body.plan, body.interval, user["email"])}
    except IntegrationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.post("/api/workspace/portal", summary="Open the Stripe customer portal")
def open_portal(request: Request, ctx=Depends(workspace_admin)) -> dict[str, str]:
    tenant, _user = ctx
    try:
        return {"url": billing.portal_url(request.app, tenant)}
    except IntegrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
