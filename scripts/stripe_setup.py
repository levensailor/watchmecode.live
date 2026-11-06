"""Create the Stripe products, prices, usage meter, and webhook endpoint for watchmecode.live.

Dry run by default: prints what it would create. With --apply it calls Stripe using
STRIPE_SECRET_KEY from the environment (or .env) and writes STRIPE_PRICES and
STRIPE_WEBHOOK_SECRET into .env. Secrets are written to the file, never printed.

Usage:
  uv run python scripts/stripe_setup.py                 # dry run
  uv run python scripts/stripe_setup.py --apply         # create in the key's mode (test or live)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.billing import _form
from app.config import PROJECT_ROOT, get_settings
from app.plans import PAID_PLANS, PLANS

WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
)
INTERVALS = {"monthly": ("month", 1), "annual": ("year", 12)}


def plan_specs(app_name: str, meter_id: str) -> list[dict]:
    """One product per plan; a licensed price and a metered overage price per interval."""
    specs = []
    for plan_id in PAID_PLANS:
        plan = PLANS[plan_id]
        for interval, (stripe_interval, months) in INTERVALS.items():
            per_month = plan.annual_price if interval == "annual" else plan.monthly_price
            specs.append(
                {
                    "key": f"{plan_id}_{interval}",
                    "product": f"{app_name} {plan.name}",
                    "price": {
                        "currency": "usd",
                        "unit_amount": per_month * months * 100,
                        "recurring": {"interval": stripe_interval},
                        "lookup_key": f"wmc_{plan_id}_{interval}",
                        "nickname": f"{plan.name} ({interval})",
                    },
                }
            )
            specs.append(
                {
                    "key": f"overage_{plan_id}_{interval}",
                    "product": f"{app_name} {plan.name}",
                    "price": {
                        "currency": "usd",
                        "recurring": {"interval": stripe_interval, "usage_type": "metered", "meter": meter_id},
                        "billing_scheme": "tiered",
                        "tiers_mode": "graduated",
                        "tiers": [
                            {"up_to": plan.credits * months, "unit_amount": 0},
                            {"up_to": "inf", "unit_amount": plan.overage * 100},
                        ],
                        "lookup_key": f"wmc_overage_{plan_id}_{interval}",
                        "nickname": f"{plan.name} overage credits ({interval})",
                    },
                }
            )
    return specs


class Stripe:
    def __init__(self, key: str, base: str) -> None:
        self.client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=30)

    def post(self, path: str, data: dict) -> dict:
        response = self.client.post(
            path, content=urlencode(_form(data)), headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        body = response.json()
        if response.status_code >= 400:
            sys.exit(f"Stripe {path} failed: {body.get('error', {}).get('message')}")
        return body

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.client.get(path, params=params).json()


def write_env(values: dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0] not in values]
    kept += [f"{key}={value}" for key, value in values.items()]
    env_path.write_text("\n".join(kept) + "\n")
    print(f"Wrote {', '.join(values)} to {Path(env_path).name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="create the objects in Stripe")
    parser.add_argument("--webhook-url", help="defaults to PUBLIC_URL + /api/stripe/webhook")
    args = parser.parse_args()
    settings = get_settings()
    webhook_url = args.webhook_url or f"{settings.public_url.rstrip('/')}/api/stripe/webhook"

    if not args.apply:
        print("Dry run. Would create:")
        print(f"- meter '{settings.stripe_meter_event}' (sum of payload.value, customer from payload.stripe_customer_id)")
        for spec in plan_specs(settings.app_name, "<meter>"):
            print(f"- {spec['key']}: {json.dumps(spec['price'])}")
        print(f"- webhook {webhook_url} for {', '.join(WEBHOOK_EVENTS)}")
        print("Re-run with --apply to create them.")
        return

    if not settings.stripe_secret_key:
        sys.exit("Set STRIPE_SECRET_KEY in .env first.")
    stripe = Stripe(settings.stripe_secret_key, settings.stripe_api_base)

    meters = stripe.get("/v1/billing/meters", {"status": "active", "limit": 100}).get("data", [])
    meter = next((m for m in meters if m.get("event_name") == settings.stripe_meter_event), None)
    if meter is None:
        meter = stripe.post(
            "/v1/billing/meters",
            {
                "display_name": "Interview credits",
                "event_name": settings.stripe_meter_event,
                "default_aggregation": {"formula": "sum"},
                "customer_mapping": {"type": "by_id", "event_payload_key": "stripe_customer_id"},
                "value_settings": {"event_payload_key": "value"},
            },
        )
    print(f"Meter: {meter['id']}")

    products: dict[str, str] = {}
    prices: dict[str, str] = {}
    for spec in plan_specs(settings.app_name, meter["id"]):
        existing = stripe.get("/v1/prices", {"lookup_keys[]": spec["price"]["lookup_key"], "active": "true"}).get("data", [])
        if existing:
            prices[spec["key"]] = existing[0]["id"]
            continue
        if spec["product"] not in products:
            products[spec["product"]] = stripe.post("/v1/products", {"name": spec["product"]})["id"]
        price = stripe.post("/v1/prices", {**spec["price"], "product": products[spec["product"]]})
        prices[spec["key"]] = price["id"]
        print(f"Price {spec['key']}: {price['id']}")

    endpoint = stripe.post(
        "/v1/webhook_endpoints",
        {"url": webhook_url, "enabled_events": list(WEBHOOK_EVENTS), "description": f"{settings.app_name} billing"},
    )
    print(f"Webhook endpoint: {endpoint['id']} -> {webhook_url}")
    write_env(
        {
            "STRIPE_PRICES": ",".join(f"{k}={v}" for k, v in prices.items()),
            "STRIPE_WEBHOOK_SECRET": endpoint["secret"],
        }
    )
    print("Add STRIPE_SECRET_KEY, STRIPE_PRICES, and STRIPE_WEBHOOK_SECRET to the Vercel project environment.")


if __name__ == "__main__":
    main()
