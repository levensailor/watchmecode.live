"""HTTP client factory. Tests inject an `httpx.MockTransport` via `app.state.http_transport`."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings

USER_AGENT = "watchmecode-integrations/1.0"


def make_client(
    settings: Settings,
    transport: httpx.BaseTransport | None,
    *,
    base_url: str,
    auth: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        auth=auth,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        timeout=settings.http_timeout_seconds,
        transport=transport,
    )


class IntegrationError(RuntimeError):
    """A third-party API call failed; message is safe to show to org admins."""
