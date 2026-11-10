"""Vercel AI Gateway chat completions with structured JSON output and a fallback model."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.integrations.http import make_client

logger = logging.getLogger(__name__)

MODEL_UNAVAILABLE = {403, 404}


class GatewayError(RuntimeError):
    """Base class; callers turn these into messages for their own audience."""


class GatewayNotConfigured(GatewayError):
    """No AI_GATEWAY_API_KEY and no Vercel OIDC token."""


class GatewayUnavailable(GatewayError):
    def __init__(self, status: int) -> None:
        super().__init__(f"AI Gateway returned {status}")
        self.status = status


class GatewayNoResponse(GatewayError):
    """Timed out, failed to connect, or returned a body without a completion."""


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def gateway_token(settings: Settings) -> str:
    """AI_GATEWAY_API_KEY if set, else the Vercel OIDC token (automatic on Vercel, `vercel env pull` locally)."""
    if settings.ai_gateway_api_key:
        return settings.ai_gateway_api_key
    try:
        from vercel.oidc import get_vercel_oidc_token_sync

        return get_vercel_oidc_token_sync()
    except Exception as exc:
        raise GatewayNotConfigured("No AI Gateway key or Vercel OIDC token") from exc


def complete_json(
    settings: Settings,
    transport: httpx.BaseTransport | None,
    messages: list[dict[str, str]],
    *,
    schema_name: str,
    schema: dict[str, Any],
    max_tokens: int,
    timeout: float,
    reasoning_effort: str = "low",
) -> Completion:
    """One completion constrained to `schema`, trying the fallback model when the primary isn't available."""
    models = [m for m in dict.fromkeys((settings.ai_gateway_model, settings.ai_gateway_fallback_model)) if m]
    token = gateway_token(settings)
    try:
        with make_client(
            settings, transport, base_url=settings.ai_gateway_base_url, headers={"Authorization": f"Bearer {token}"}
        ) as client:
            for index, model_id in enumerate(models):
                payload = {
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "reasoning_effort": reasoning_effort,
                    "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
                }
                response = client.post("/chat/completions", json=payload, timeout=timeout)
                if response.status_code in MODEL_UNAVAILABLE and index + 1 < len(models):
                    logger.info("Model %s unavailable (%s); falling back", model_id, response.status_code)
                    continue
                break
        if response.status_code >= 400:
            message = None
            try:
                message = (response.json().get("error") or {}).get("message") if response.content else None
            except ValueError:
                pass
            logger.warning("AI Gateway returned %s: %s", response.status_code, message)
            raise GatewayUnavailable(response.status_code)
        body = response.json()
        choice = body["choices"][0]
        completion = Completion(
            content=choice["message"]["content"] or "",
            model=body.get("model") or model_id,
            finish_reason=choice.get("finish_reason"),
            usage=body.get("usage") or {},
        )
    except GatewayError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("AI Gateway call failed: %s", exc.__class__.__name__)
        raise GatewayNoResponse(exc.__class__.__name__) from exc
    if completion.finish_reason == "length":
        logger.warning("AI Gateway hit the output limit with %s", completion.model)
    return completion
