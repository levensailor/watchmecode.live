"""Transactional email through Resend (optional; skipped when not configured)."""

from __future__ import annotations

import html
import logging

import httpx

from app.config import Settings
from app.integrations.http import make_client

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com"


def email_configured(settings: Settings) -> bool:
    return bool(settings.resend_api_key and settings.email_from)


def send_email(
    settings: Settings,
    to: str,
    subject: str,
    html_body: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if not email_configured(settings):
        logger.info("Email not configured; skipped sending %r", subject)
        return False
    with make_client(
        settings,
        transport,
        base_url=RESEND_API,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
    ) as client:
        try:
            response = client.post(
                "/emails", json={"from": settings.email_from, "to": [to], "subject": subject, "html": html_body}
            )
        except httpx.HTTPError as exc:
            logger.warning("Email send failed: %s", exc)
            return False
    if response.status_code >= 400:
        logger.warning("Email send failed with HTTP %s", response.status_code)
        return False
    return True


def take_home_invite(app_name: str, candidate_name: str, title: str, link: str, minutes: int | None) -> tuple[str, str]:
    limit = f"<p>You'll have <strong>{minutes} minutes</strong> once you open it.</p>" if minutes else ""
    subject = f"Your coding assessment: {title}"
    body = (
        f"<p>Hi {html.escape(candidate_name)},</p>"
        f"<p>You've been invited to complete <strong>{html.escape(title)}</strong> on {html.escape(app_name)}.</p>"
        f"{limit}"
        f'<p><a href="{html.escape(link)}">Open your assessment</a></p>'
        "<p>Your code runs in the browser IDE. Press <em>Submit</em> when you're done.</p>"
    )
    return subject, body
