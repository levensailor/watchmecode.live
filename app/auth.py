"""Google OpenID Connect sign-in for interviewers and signed tokens for candidates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings
from app.schemas import UserOut

logger = logging.getLogger(__name__)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SESSION_USER_KEY = "user"
TOKEN_SALT = "participant-token"

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


def session_user(request: Request) -> dict | None:
    user = request.session.get(SESSION_USER_KEY)
    return user if isinstance(user, dict) and user.get("email") else None


def require_user(request: Request) -> dict:
    user = session_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    return user


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _redirect_uri(request: Request, settings: Settings) -> str:
    return settings.google_redirect_uri or str(request.url_for("google_callback"))


@router.get("/auth/google", include_in_schema=True, summary="Start Google sign-in (redirects to Google)")
async def google_login(request: Request):
    settings = _settings(request)
    if not settings.google_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    oauth: OAuth = request.app.state.oauth
    return await oauth.google.authorize_redirect(request, _redirect_uri(request, settings))


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
    request.session[SESSION_USER_KEY] = {
        "name": info.get("name") or info.get("email"),
        "email": info.get("email"),
        "picture": info.get("picture"),
    }
    logger.info("Interviewer signed in: %s", info.get("email"))
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


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
