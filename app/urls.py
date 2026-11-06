"""Absolute URLs for links that leave the app (invites, ATS notes, API responses)."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.tenancy import current_tenant, tenant_url


def base_url(settings: Settings, request: Request | None = None) -> str:
    tenant = current_tenant.get()
    if settings.multi_tenant and tenant is not None:
        return tenant_url(settings, tenant)
    if settings.public_url:
        return settings.public_url.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


def join_url(base: str, token: str) -> str:
    return f"{base}/j/{token}"


def interviewer_url(base: str, session_id: str) -> str:
    return f"{base}/#/s/{session_id}"


def playback_url(base: str, session_id: str, recording_id: str) -> str:
    return f"{base}/#/s/{session_id}/play/{recording_id}"


def files_url(base: str, session_id: str) -> str:
    return f"{base}/api/sessions/{session_id}/files.zip"


def ashby_webhook_url(base: str, org_slug: str) -> str:
    return f"{base}/integrations/ashby/webhook/{org_slug}"


def ashby_partner_base_url(base: str) -> str:
    return f"{base}/partners/ashby"
