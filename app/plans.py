"""Plans, entitlements, and interview-credit metering."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import Settings
from app.tenancy import current_tenant

logger = logging.getLogger(__name__)

STARTER_FEATURES = frozenset({"live", "languages", "web", "recordings", "invite_links", "portal", "take_home"})
TEAM_FEATURES = STARTER_FEATURES | {
    "integrations",
    "api",
    "webhooks",
    "unlimited_take_home",
    "labs_browser",
    "labs_sandbox",
    "ai_summary",
    "branding",
}
ENTERPRISE_FEATURES = TEAM_FEATURES | {"dedicated", "sso", "audit", "custom_domain", "labs_cloud"}

FEATURE_LABELS = {
    "integrations": "ATS and scheduling integrations",
    "api": "the public API",
    "webhooks": "outbound webhooks",
    "unlimited_take_home": "unlimited take-homes",
    "labs_browser": "browser labs",
    "labs_sandbox": "sandbox labs",
    "ai_summary": "AI evaluation summaries",
    "branding": "custom branding",
    "sso": "SSO",
    "custom_domain": "a custom domain",
}

LAB_LANGUAGES = {"sql": "labs_browser", "react": "labs_browser", "whiteboard": "labs_browser"}
SANDBOX_CREDITS = 2


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price: int | None
    annual_price: int | None
    credits: int
    overage: int | None
    take_homes: int | None
    features: frozenset[str]
    blurb: str


PLANS: dict[str, Plan] = {
    "trial": Plan("trial", "Free trial", 0, 0, 15, None, None, TEAM_FEATURES, "14 days of Team, no card"),
    "starter": Plan("starter", "Starter", 59, 49, 10, 12, 3, STARTER_FEATURES, "For teams starting to hire engineers"),
    "team": Plan("team", "Team", 299, 249, 50, 10, None, TEAM_FEATURES, "For teams that hire every week"),
    "enterprise": Plan(
        "enterprise", "Enterprise", None, None, 500, None, None, ENTERPRISE_FEATURES, "Dedicated instance, SSO, SLA"
    ),
}
PAID_PLANS = ("starter", "team")
MINIMUM_PLAN = {feature: next(p for p in ("starter", "team", "enterprise") if feature in PLANS[p].features) for feature in ENTERPRISE_FEATURES}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def plan_for(tenant: dict[str, Any] | None) -> Plan:
    if tenant is None:
        return PLANS["enterprise"]
    return PLANS.get(tenant.get("plan") or "trial", PLANS["trial"])


def state_of(tenant: dict[str, Any] | None, now: datetime | None = None) -> str:
    """active | trialing | expired | past_due | canceled"""
    if tenant is None:
        return "active"
    status_ = tenant.get("status") or "trialing"
    if status_ == "trialing":
        ends = _parse(tenant.get("trial_ends_at"))
        if ends and ends <= (now or datetime.now(UTC)):
            return "expired"
    return status_


def trial_days_left(tenant: dict[str, Any] | None, now: datetime | None = None) -> int | None:
    ends = _parse((tenant or {}).get("trial_ends_at"))
    if not ends or (tenant or {}).get("status") != "trialing":
        return None
    seconds = (ends - (now or datetime.now(UTC))).total_seconds()
    return max(0, int((seconds + 86399) // 86400))


def period_start(tenant: dict[str, Any], now: datetime | None = None) -> str:
    """Start of the current credit period: the billing period, the trial start, or the calendar month."""
    if tenant.get("current_period_start"):
        return tenant["current_period_start"]
    if tenant.get("status") == "trialing":
        return tenant.get("created_at") or ""
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def has_feature(settings: Settings, tenant: dict[str, Any] | None, feature: str) -> bool:
    if not settings.multi_tenant or tenant is None:
        return True
    if state_of(tenant) in ("expired", "canceled"):
        return False
    return feature in plan_for(tenant).features


def can_create_sessions(settings: Settings, tenant: dict[str, Any] | None) -> bool:
    return not settings.multi_tenant or tenant is None or state_of(tenant) in ("active", "trialing", "past_due")


def upgrade_message(feature: str) -> str:
    plan = PLANS[MINIMUM_PLAN.get(feature, "team")].name
    return f"{FEATURE_LABELS.get(feature, feature).capitalize()} is included in the {plan} plan."


def require_feature(feature: str):
    """FastAPI dependency: 402 when the current workspace's plan doesn't include `feature`."""

    def dependency(request: Request) -> None:
        settings: Settings = request.app.state.settings
        tenant = current_tenant.get()
        if not has_feature(settings, tenant, feature):
            if state_of(tenant) in ("expired", "canceled"):
                raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Your trial has ended. Choose a plan to continue.")
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, upgrade_message(feature))

    return dependency


def require_active(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not can_create_sessions(settings, current_tenant.get()):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Your trial has ended. Choose a plan to continue.")


def check_language(settings: Settings, language: str) -> None:
    feature = LAB_LANGUAGES.get(language)
    if feature and not has_feature(settings, current_tenant.get(), feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, upgrade_message(feature))


def check_take_home_quota(settings: Settings, registry, tenant: dict[str, Any] | None) -> None:
    if not settings.multi_tenant or tenant is None or registry.platform is None:
        return
    plan = plan_for(tenant)
    if plan.take_homes is None:
        return
    used = registry.platform.count_usage(tenant["id"], period_start(tenant), "take_home")
    if used >= plan.take_homes:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"{plan.name} includes {plan.take_homes} take-homes a month. Team includes unlimited take-homes.",
        )


def credits_for(language: str, sandbox: bool = False) -> int:
    return SANDBOX_CREDITS if sandbox else 1


def usage_summary(settings: Settings, registry, tenant: dict[str, Any] | None) -> dict[str, Any]:
    plan = plan_for(tenant)
    if not settings.multi_tenant or tenant is None or registry.platform is None:
        return {"plan": plan.id, "plan_name": plan.name, "credits_included": None, "credits_used": 0}
    used = registry.platform.credits_used(tenant["id"], period_start(tenant))
    return {
        "plan": plan.id,
        "plan_name": plan.name,
        "state": state_of(tenant),
        "trial_days_left": trial_days_left(tenant),
        "credits_included": plan.credits,
        "credits_used": used,
        "overage_rate": plan.overage,
        "period_start": period_start(tenant),
    }


def check_join_allowed(settings: Settings, registry, tenant: dict[str, Any] | None, session: dict[str, Any]) -> None:
    """Trials stop at their credit allowance; paid plans keep going and bill overage."""
    if not settings.multi_tenant or tenant is None or registry.platform is None:
        return
    if registry.platform.session_metered(tenant["id"], session["id"]):
        return
    state = state_of(tenant)
    if state in ("expired", "canceled"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "This workspace's trial has ended.")
    if state == "trialing":
        used = registry.platform.credits_used(tenant["id"], period_start(tenant))
        if used + credits_for(session["language"]) > plan_for(tenant).credits:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED, "This workspace has used all of its trial interview credits."
            )


def meter_join(app, tenant: dict[str, Any] | None, session: dict[str, Any], sandbox: bool = False) -> dict[str, Any] | None:
    """Count one candidate join per session; reports overage to Stripe for paid plans."""
    registry = app.state.tenants
    if tenant is None or registry.platform is None:
        return None
    kind = "take_home" if session.get("mode") == "take_home" else "candidate_join"
    event = registry.platform.record_usage(tenant["id"], session["id"], kind, credits_for(session["language"], sandbox))
    if event:
        logger.info("Metered %s credit(s) for %s in %s", event["credits"], session["id"], tenant["slug"])
        from app import billing

        billing.report_usage(app, tenant, event)
    return event
