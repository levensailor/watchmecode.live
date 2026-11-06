"""Google OpenID Connect sign-in for interviewers and signed tokens for candidates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from urllib.parse import quote, urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import handoff
from app.config import Settings
from app.schemas import UserOut
from app.tenancy import apex_url, current_site, current_tenant, tenant_url

logger = logging.getLogger(__name__)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SESSION_USER_KEY = "user"
HANDOFF_KEY = "handoff"
AFTER_LOGIN_KEY = "after_login"
TOKEN_SALT = "participant-token"
JOIN_LINK_SALT = "join-link"

router = APIRouter(tags=["auth"])


@dataclass(frozen=True)
class Participant:
    role: Literal["interviewer", "candidate"]
    label: str
    session_id: str


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    if settings.google_configured:
        oauth.register(
            "google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url=GOOGLE_DISCOVERY_URL,
            client_kwargs={"scope": settings.google_scopes, "code_challenge_method": "S256"},
        )
    return oauth


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=TOKEN_SALT)


def issue_participant_token(settings: Settings, session_id: str) -> str:
    return _serializer(settings).dumps({"sid": session_id, "role": "candidate"})


def read_participant_token(settings: Settings, token: str) -> str | None:
    try:
        data = _serializer(settings).loads(token, max_age=settings.participant_token_max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("sid") if isinstance(data, dict) else None


def issue_join_token(settings: Settings, session_id: str) -> str:
    """Long-lived invite token for candidate join links (exchanged for a participant token)."""
    return URLSafeTimedSerializer(settings.session_secret, salt=JOIN_LINK_SALT).dumps({"sid": session_id})


def read_join_token(settings: Settings, token: str) -> str | None:
    try:
        data = URLSafeTimedSerializer(settings.session_secret, salt=JOIN_LINK_SALT).loads(
            token, max_age=settings.join_link_max_age
        )
    except (BadSignature, SignatureExpired):
        return None
    return data.get("sid") if isinstance(data, dict) else None


def session_user(request: Request) -> dict | None:
    user = request.session.get(SESSION_USER_KEY)
    return user if isinstance(user, dict) and user.get("email") else None


SESSION_TENANT_KEY = "tenant"


def _workspace_check(request: Request, user: dict) -> int | None:
    """None when the user may act in the current workspace, else the HTTP status to refuse with."""
    tenant = current_tenant.get()
    registry = getattr(request.app.state, "tenants", None)
    if tenant is None or registry is None or not registry.enabled:
        return None
    if current_site.get() == "apex" or request.session.get(SESSION_TENANT_KEY) != tenant["slug"]:
        return status.HTTP_401_UNAUTHORIZED
    if not registry.role_for(tenant, user["email"]):
        return status.HTTP_403_FORBIDDEN
    return None


def workspace_user(request: Request) -> dict | None:
    user = session_user(request)
    return user if user and _workspace_check(request, user) is None else None


def require_user(request: Request) -> dict:
    user = session_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    refused = _workspace_check(request, user)
    if refused == status.HTTP_403_FORBIDDEN:
        raise HTTPException(refused, "You're not a member of this workspace")
    if refused:
        raise HTTPException(refused, "Sign in required")
    return user


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _redirect_uri(request: Request, settings: Settings) -> str:
    return settings.google_redirect_uri or str(request.url_for("google_callback"))


def safe_next(value: str | None) -> str:
    """Only same-site relative paths survive a redirect round trip."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def _hub_url(settings: Settings) -> str | None:
    """Where workspace hosts send sign-in: the apex in multi mode, AUTH_HUB_URL for dedicated instances."""
    if settings.tenant_mode == "dedicated" and settings.auth_hub_url:
        return settings.auth_hub_url.rstrip("/")
    if settings.multi_tenant and current_site.get() == "tenant":
        return apex_url(settings)
    return None


def _origin(request: Request) -> str:
    settings = _settings(request)
    tenant = current_tenant.get()
    if settings.multi_tenant and tenant is not None and current_site.get() == "tenant":
        return tenant_url(settings, tenant)
    return (settings.public_url or str(request.base_url)).rstrip("/")


def _workspace_slug(request: Request) -> str:
    tenant = current_tenant.get()
    return tenant["slug"] if tenant is not None else _settings(request).tenant_slug


@router.get("/auth/google", include_in_schema=True, summary="Start Google sign-in (redirects to Google)")
async def google_login(request: Request, tenant: str | None = None, next: str | None = None):
    settings = _settings(request)
    hub = _hub_url(settings)
    if hub:
        query = urlencode({"tenant": _workspace_slug(request), "next": safe_next(next)})
        return RedirectResponse(f"{hub}/auth/google?{query}", status_code=status.HTTP_303_SEE_OTHER)
    if not settings.google_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    if tenant:
        request.session[HANDOFF_KEY] = {"tenant": tenant, "next": safe_next(next)}
    else:
        request.session.pop(HANDOFF_KEY, None)
        request.session[AFTER_LOGIN_KEY] = safe_next(next) if next else None
    oauth: OAuth = request.app.state.oauth
    return await oauth.google.authorize_redirect(request, _redirect_uri(request, settings))


def handoff_redirect(request: Request, user: dict, slug: str, next_path: str = "/") -> RedirectResponse:
    """From the hub: send a signed-in user into workspace `slug`, or explain why not."""
    settings = _settings(request)
    registry = request.app.state.tenants
    tenant = registry.tenant(slug) if registry.enabled else None
    if tenant is None or tenant["status"] == "deleted":
        return RedirectResponse(f"/login?error=unknown_workspace&tenant={quote(slug)}", status_code=303)
    if not registry.role_for(tenant, user["email"]):
        logger.info("Handoff refused: %s is not a member of %s", user["email"], slug)
        return RedirectResponse(f"/login?error=not_member&tenant={quote(slug)}", status_code=303)
    audience = tenant_url(settings, tenant)
    token = handoff.issue(settings, user, slug, audience)
    query = urlencode({"token": token, "next": safe_next(next_path)})
    return RedirectResponse(f"{audience}/auth/handoff?{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/google/callback", name="google_callback", summary="Google OAuth callback")
async def google_callback(request: Request):
    settings = _settings(request)
    if not settings.google_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    oauth: OAuth = request.app.state.oauth
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        logger.warning("Google sign-in failed: %s", exc.error)
        return RedirectResponse(f"/?auth_error={exc.error}", status_code=status.HTTP_303_SEE_OTHER)
    info = token.get("userinfo") or await oauth.google.userinfo(token=token)
    user = {"name": info.get("name") or info.get("email"), "email": info.get("email"), "picture": info.get("picture")}
    request.session[SESSION_USER_KEY] = user
    logger.info("Interviewer signed in: %s", info.get("email"))
    pending = request.session.pop(HANDOFF_KEY, None)
    after = request.session.pop(AFTER_LOGIN_KEY, None)
    registry = request.app.state.tenants
    if registry.enabled:
        if isinstance(pending, dict) and pending.get("tenant"):
            return await run_in_threadpool(handoff_redirect, request, user, pending["tenant"], pending.get("next") or "/")
        return RedirectResponse(after or "/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(after or "/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/handoff", summary="Finish sign-in on a workspace with a token from the auth hub")
def finish_handoff(request: Request, token: str, next: str | None = None):
    settings = _settings(request)
    slug = _workspace_slug(request)
    user = handoff.verify(settings, token, slug, _origin(request))
    if user is None:
        logger.warning("Rejected sign-in handoff for %s", slug)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This sign-in link is invalid or expired. Sign in again.")
    request.session.clear()
    request.session[SESSION_USER_KEY] = user
    request.session[SESSION_TENANT_KEY] = slug
    logger.info("Interviewer %s signed in to %s via the auth hub", user["email"], slug)
    return RedirectResponse(safe_next(next), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/logout", summary="Clear the interviewer session")
async def logout(request: Request):
    user = session_user(request)
    request.session.clear()
    if user:
        logger.info("Interviewer signed out: %s", user.get("email"))
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/api/me", response_model=UserOut, summary="Current signed-in interviewer")
def me(user: dict = Depends(require_user)) -> UserOut:
    return UserOut(**user)
