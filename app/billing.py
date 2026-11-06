"""Stripe billing: Checkout for upgrades, the Customer Portal, webhooks, and metered overage.

Talks to the Stripe REST API directly (form-encoded) through the shared httpx client factory,
so tests can swap in an `httpx.MockTransport`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.integrations.http import IntegrationError, make_client
from app.plans import PAID_PLANS, plan_for
from app.tenancy import tenant_url

logger = logging.getLogger(__name__)

SIGNATURE_TOLERANCE_SECONDS = 300
INTERVALS = ("monthly", "annual")
SUBSCRIPTION_STATUS = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
    "paused": "past_due",
}


def price_map(settings: Settings) -> dict[str, str]:
    """STRIPE_PRICES="starter_monthly=price_..,starter_annual=..,team_monthly=..,team_annual=..,
    overage_starter_monthly=..,overage_starter_annual=..,overage_team_monthly=..,overage_team_annual=.."
    """
    pairs = (item.split("=", 1) for item in settings.stripe_prices.split(",") if "=" in item)
    return {key.strip(): value.strip() for key, value in pairs if key.strip() and value.strip()}


def plan_for_price(settings: Settings, price_id: str) -> tuple[str, str] | None:
    for key, value in price_map(settings).items():
        if value == price_id and not key.startswith("overage_"):
            plan, _, interval = key.partition("_")
            return plan, interval
    return None


def _form(data: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Flatten nested dicts/lists into Stripe's bracketed form fields."""
    fields: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            fields.extend(_form(value, name))
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    fields.extend(_form(item, f"{name}[{index}]"))
                else:
                    fields.append((f"{name}[{index}]", str(item)))
        elif isinstance(value, bool):
            fields.append((name, "true" if value else "false"))
        else:
            fields.append((name, str(value)))
    return fields


class StripeClient:
    def __init__(self, app: FastAPI) -> None:
        settings: Settings = app.state.settings
        if not settings.stripe_configured:
            raise IntegrationError("Billing is not configured on this server.")
        self._client = make_client(
            settings,
            getattr(app.state, "http_transport", None),
            base_url=settings.stripe_api_base,
            headers={"Authorization": f"Bearer {settings.stripe_secret_key}"},
        )

    def __enter__(self) -> StripeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def post(self, path: str, data: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self._client.post(path, content=urlencode(_form(data)), headers=headers)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Stripe is unreachable: {exc.__class__.__name__}") from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            message = (body.get("error") or {}).get("message") or f"HTTP {response.status_code}"
            logger.warning("Stripe %s failed: %s", path, message)
            raise IntegrationError(f"Stripe: {message}")
        return body


def _unix(iso: str | None) -> int | None:
    if not iso:
        return None
    parsed = datetime.fromisoformat(iso)
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds") if ts else None


def checkout_url(app: FastAPI, tenant: dict[str, Any], plan: str, interval: str, email: str) -> str:
    settings: Settings = app.state.settings
    if plan not in PAID_PLANS or interval not in INTERVALS:
        raise IntegrationError("Choose Starter or Team, monthly or annual.")
    prices = price_map(settings)
    base_price = prices.get(f"{plan}_{interval}")
    if not base_price:
        raise IntegrationError(f"No Stripe price is configured for {plan} {interval}.")
    line_items: list[dict[str, Any]] = [{"price": base_price, "quantity": 1}]
    overage = prices.get(f"overage_{plan}_{interval}")
    if overage:
        line_items.append({"price": overage})
    workspace = tenant_url(settings, tenant)
    subscription_data: dict[str, Any] = {"metadata": {"tenant_id": tenant["id"], "plan": plan, "interval": interval}}
    trial_end = _unix(tenant.get("trial_ends_at")) if tenant.get("status") == "trialing" else None
    if trial_end and trial_end > time.time() + 2 * 86400:
        subscription_data["trial_end"] = trial_end
    data: dict[str, Any] = {
        "mode": "subscription",
        "line_items": line_items,
        "success_url": f"{workspace}/#/workspace?billing=success",
        "cancel_url": f"{workspace}/#/workspace?billing=cancelled",
        "client_reference_id": tenant["id"],
        "metadata": {"tenant_id": tenant["id"], "plan": plan, "interval": interval},
        "subscription_data": subscription_data,
        "allow_promotion_codes": True,
    }
    if tenant.get("stripe_customer_id"):
        data["customer"] = tenant["stripe_customer_id"]
    else:
        data["customer_email"] = email
    with StripeClient(app) as stripe:
        session = stripe.post("/v1/checkout/sessions", data)
    logger.info("Checkout session %s for %s (%s %s)", session.get("id"), tenant["slug"], plan, interval)
    return session["url"]


def portal_url(app: FastAPI, tenant: dict[str, Any]) -> str:
    if not tenant.get("stripe_customer_id"):
        raise IntegrationError("This workspace has no billing account yet. Choose a plan first.")
    workspace = tenant_url(app.state.settings, tenant)
    with StripeClient(app) as stripe:
        session = stripe.post(
            "/v1/billing_portal/sessions",
            {"customer": tenant["stripe_customer_id"], "return_url": f"{workspace}/#/workspace"},
        )
    return session["url"]


def report_usage(app: FastAPI, tenant: dict[str, Any], event: dict[str, Any]) -> bool:
    """Send a meter event for paid plans; Stripe's graduated tiers bill only credits past the allowance."""
    settings: Settings = app.state.settings
    customer = tenant.get("stripe_customer_id")
    if not settings.stripe_configured or not customer or plan_for(tenant).id not in PAID_PLANS:
        return False
    data = {
        "event_name": settings.stripe_meter_event,
        "identifier": event["id"],
        "timestamp": _unix(event["created_at"]),
        "payload": {"stripe_customer_id": customer, "value": event["credits"]},
    }
    try:
        with StripeClient(app) as stripe:
            stripe.post("/v1/billing/meter_events", data, idempotency_key=event["id"])
    except IntegrationError as exc:
        logger.warning("Usage report for %s failed: %s", tenant["slug"], exc)
        return False
    app.state.tenants.platform.mark_usage_reported(event["id"])
    return True


def verify_webhook(secret: str, payload: bytes, header: str | None, now: float | None = None) -> bool:
    """Stripe-Signature: t=<ts>,v1=<hex HMAC-SHA256 of "<ts>.<payload>">."""
    if not secret or not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts.get("t", ["0"])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


def _subscription_fields(settings: Settings, subscription: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "stripe_subscription_id": subscription.get("id"),
        "status": SUBSCRIPTION_STATUS.get(subscription.get("status", ""), "past_due"),
    }
    items = (subscription.get("items") or {}).get("data") or []
    for item in items:
        match = plan_for_price(settings, (item.get("price") or {}).get("id", ""))
        if match:
            fields["plan"], fields["billing_interval"] = match
        start = item.get("current_period_start") or subscription.get("current_period_start")
        end = item.get("current_period_end") or subscription.get("current_period_end")
        if start:
            fields["current_period_start"] = _iso(start)
            fields["current_period_end"] = _iso(end)
    metadata = subscription.get("metadata") or {}
    if "plan" not in fields and metadata.get("plan") in PAID_PLANS:
        fields["plan"] = metadata["plan"]
        fields["billing_interval"] = metadata.get("interval")
    if subscription.get("customer"):
        fields["stripe_customer_id"] = subscription["customer"]
    return fields


def apply_event(app: FastAPI, event: dict[str, Any]) -> str:
    """Update the tenant a Stripe event belongs to. Returns a short outcome for logs and tests."""
    settings: Settings = app.state.settings
    registry = app.state.tenants
    platform = registry.platform
    obj = (event.get("data") or {}).get("object") or {}
    event_type = event.get("type", "")

    def find_tenant() -> dict[str, Any] | None:
        tenant_id = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
        if tenant_id:
            found = platform.get_tenant_by_id(tenant_id)
            if found:
                return found
        customer = obj.get("customer")
        return platform.get_tenant_by_customer(customer) if customer else None

    tenant = find_tenant()
    if not platform.record_stripe_event(event.get("id", ""), event_type, tenant["id"] if tenant else None):
        return "duplicate"
    if tenant is None:
        logger.info("Stripe %s has no matching workspace", event_type)
        return "ignored"

    updates: dict[str, Any] = {}
    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        updates = {
            "stripe_customer_id": obj.get("customer"),
            "stripe_subscription_id": obj.get("subscription"),
            "status": "active",
        }
        if metadata.get("plan") in PAID_PLANS:
            updates["plan"] = metadata["plan"]
            updates["billing_interval"] = metadata.get("interval")
    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        updates = _subscription_fields(settings, obj)
    elif event_type == "customer.subscription.deleted":
        updates = {"status": "canceled"}
    elif event_type == "invoice.payment_failed":
        updates = {"status": "past_due"}
    elif event_type == "invoice.paid" and tenant["status"] == "past_due":
        updates = {"status": "active"}
    else:
        return "ignored"

    updates = {k: v for k, v in updates.items() if v is not None}
    platform.update_tenant(tenant["id"], **updates)
    registry.invalidate(tenant["slug"])
    logger.info("Stripe %s applied to %s: %s", event_type, tenant["slug"], sorted(updates))
    return "applied"
