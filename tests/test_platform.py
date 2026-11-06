from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import handoff
from app.main import create_app
from app.plans import state_of
from tests.conftest import INTERVIEWER, OTHER_INTERVIEWER, ROOT, tenant_client

WEBHOOK_SECRET = "whsec_test"
PRICES = (
    "starter_monthly=price_sm,starter_annual=price_sa,team_monthly=price_tm,team_annual=price_ta,"
    "overage_team_annual=price_ota,overage_team_monthly=price_otm"
)


class FakeStripe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]]]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.calls.append((request.url.path, form))
        if request.url.path == "/v1/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"})
        if request.url.path == "/v1/billing_portal/sessions":
            return httpx.Response(200, json={"id": "bps_1", "url": "https://billing.stripe.test/p/1"})
        if request.url.path == "/v1/billing/meter_events":
            return httpx.Response(200, json={"identifier": form["identifier"][0]})
        return httpx.Response(404, json={"error": {"message": "unknown"}})


@pytest.fixture
def stripe():
    return FakeStripe()


@pytest.fixture
def billing_app(multi_settings, stripe):
    app = create_app(
        replace(multi_settings, stripe_secret_key="sk_test", stripe_webhook_secret=WEBHOOK_SECRET, stripe_prices=PRICES)
    )
    app.state.http_transport = httpx.MockTransport(stripe.handle)
    return app


def hub(app, settings, user=None):
    return tenant_client(app, settings, None, user)


def signed(payload: dict, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


# Handoff tokens ---------------------------------------------------------------------------


def test_handoff_token_round_trip_and_single_use(multi_settings):
    token = handoff.issue(multi_settings, INTERVIEWER, "acme", f"https://acme.{ROOT}")
    user = handoff.verify(multi_settings, token, "acme", f"https://acme.{ROOT}")
    assert user == {"name": INTERVIEWER["name"], "email": INTERVIEWER["email"], "picture": None}
    assert handoff.verify(multi_settings, token, "acme", f"https://acme.{ROOT}") is None


def test_handoff_token_rejects_other_workspace_origin_expiry_and_tampering(multi_settings):
    audience = f"https://acme.{ROOT}"
    assert handoff.verify(multi_settings, handoff.issue(multi_settings, INTERVIEWER, "acme", audience), "globex", audience) is None
    assert handoff.verify(multi_settings, handoff.issue(multi_settings, INTERVIEWER, "acme", audience), "acme", "https://evil.test") is None
    old = handoff.issue(multi_settings, INTERVIEWER, "acme", audience, now=time.time() - 3600)
    assert handoff.verify(multi_settings, old, "acme", audience) is None
    body, sig = handoff.issue(multi_settings, INTERVIEWER, "acme", audience).split(".")
    assert handoff.verify(multi_settings, f"{body[:-2]}xx.{sig}", "acme", audience) is None


def test_dedicated_instances_verify_with_only_the_public_key(multi_settings):
    token = handoff.issue(multi_settings, INTERVIEWER, "bigco", "https://code.bigco.com")
    dedicated = replace(multi_settings, session_secret="different", handoff_public_key=handoff.public_key_b64(multi_settings))
    assert handoff.verify(dedicated, token, "bigco", "https://code.bigco.com")["email"] == INTERVIEWER["email"]


def test_workspace_login_redirects_to_hub_and_handoff_signs_in(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    acme = tenant_client(multi_app, multi_settings, "acme")
    response = acme.get("/auth/google?next=/%23/org", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"https://{ROOT}/auth/google?tenant=acme&next=%2F%23%2Forg"

    go = hub(multi_app, multi_settings, INTERVIEWER).get("/api/platform/go/acme?next=/", follow_redirects=False)
    location = urlsplit(go.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"https://acme.{ROOT}/auth/handoff"
    token = parse_qs(location.query)["token"][0]

    finished = acme.get(f"/auth/handoff?token={token}&next=/", follow_redirects=False)
    assert finished.status_code == 303
    assert acme.get("/api/me").json()["email"] == INTERVIEWER["email"]
    assert acme.get(f"/auth/handoff?token={token}").status_code == 401


def test_hub_refuses_handoff_for_non_members(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    go = hub(multi_app, multi_settings, OTHER_INTERVIEWER).get("/api/platform/go/acme", follow_redirects=False)
    assert go.headers["location"] == "/login?error=not_member&tenant=acme"


def test_public_key_is_published(multi_app, multi_settings):
    body = hub(multi_app, multi_settings).get("/.well-known/watchmecode-handoff.json").json()
    assert body == {"alg": "Ed25519", "public_key": handoff.public_key_b64(multi_settings)}


# Signup ---------------------------------------------------------------------------------


def test_signup_checks_subdomains_and_creates_a_trial_workspace(multi_app, multi_settings):
    client = hub(multi_app, multi_settings, INTERVIEWER)
    assert client.get("/api/platform/subdomains/ae").json() == {"slug": "ae", "available": True, "reason": None}
    assert client.get("/api/platform/subdomains/www").json()["available"] is False

    assert hub(multi_app, multi_settings).post("/api/platform/workspaces", json={"name": "AE", "slug": "ae"}).status_code == 401
    created = client.post("/api/platform/workspaces", json={"name": "American Eagle", "slug": "ae"})
    assert created.status_code == 201
    body = created.json()
    assert body["url"] == f"https://ae.{ROOT}"
    assert body["plan"] == "trial" and body["state"] == "trialing" and body["trial_days_left"] == 14
    assert body["role"] == "owner"

    assert client.post("/api/platform/workspaces", json={"name": "Dup", "slug": "ae"}).status_code == 409
    assert client.get("/api/platform/subdomains/ae").json()["reason"] == "That subdomain is taken."
    assert [w["slug"] for w in client.get("/api/platform/me").json()["workspaces"]] == ["ae"]


def test_platform_endpoints_are_apex_only(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    assert tenant_client(multi_app, multi_settings, "acme").get("/api/platform/me").status_code == 404


# Workspace admin ----------------------------------------------------------------------


def test_workspace_admins_manage_members_and_domains(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    info = owner.get("/api/workspace").json()
    assert info["role"] == "owner" and info["usage"]["credits_included"] == 15

    assert owner.post("/api/workspace/members", json={"email": OTHER_INTERVIEWER["email"]}).status_code == 201
    member = tenant_client(multi_app, multi_settings, "acme", OTHER_INTERVIEWER)
    assert member.get("/api/me").status_code == 200
    assert member.put("/api/workspace", json={"name": "Nope"}).status_code == 403

    updated = owner.put("/api/workspace", json={"allowed_domains": ["@Acme.com", "acme.io"]}).json()
    assert updated["allowed_domains"] == ["acme.com", "acme.io"]
    assert owner.put("/api/workspace", json={"allowed_domains": ["not a domain"]}).status_code == 422

    assert owner.delete(f"/api/workspace/members/{OTHER_INTERVIEWER['email']}").status_code == 204
    assert owner.delete(f"/api/workspace/members/{INTERVIEWER['email']}").status_code == 404


# Entitlements and metering ----------------------------------------------------------


def test_trial_credits_are_metered_once_per_session_and_capped(multi_app, multi_settings):
    registry = multi_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    candidate = tenant_client(multi_app, multi_settings, "acme")

    pins = [owner.post("/api/sessions", json={"candidate_name": f"C{i}", "language": "python"}).json()["pin"] for i in range(16)]
    for pin in pins[:15]:
        assert candidate.post("/api/join", json={"pin": pin}).status_code == 200
    assert candidate.post("/api/join", json={"pin": pins[0]}).status_code == 200
    assert registry.platform.credits_used(tenant["id"]) == 15
    blocked = candidate.post("/api/join", json={"pin": pins[15]})
    assert blocked.status_code == 402
    assert "trial interview credits" in blocked.json()["detail"]


def test_expired_trial_blocks_new_sessions_and_integrations(multi_app, multi_settings):
    registry = multi_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    registry.platform.update_tenant(tenant["id"], trial_ends_at="2020-01-01T00:00:00+00:00")
    registry.invalidate("acme")
    assert state_of(registry.tenant("acme")) == "expired"
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    assert owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "python"}).status_code == 402
    org = owner.post("/api/orgs", json={"name": "Acme Eng"}).json()
    assert owner.put(f"/api/orgs/{org['id']}/integrations/modernloop", json={"api_token": "t"}).status_code == 402


def test_starter_plan_has_no_integrations_or_api(multi_app, multi_settings):
    registry = multi_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    registry.platform.update_tenant(tenant["id"], plan="starter", status="active")
    registry.invalidate("acme")
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    org = owner.post("/api/orgs", json={"name": "Acme Eng"}).json()
    blocked = owner.put(f"/api/orgs/{org['id']}/integrations/ashby", json={"api_key": "k"})
    assert blocked.status_code == 402
    assert "Team plan" in blocked.json()["detail"]
    key = owner.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "ci"}).json()["key"]
    assert key.startswith("wmc_acme.")
    assert hub(multi_app, multi_settings).get("/v1/pads", headers={"Authorization": f"Bearer {key}"}).status_code == 402


def test_scoped_api_keys_route_through_the_apex(multi_app, multi_settings):
    multi_app.state.tenants.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    org = owner.post("/api/orgs", json={"name": "Acme Eng"}).json()
    key = owner.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "ci"}).json()["key"]
    pad = hub(multi_app, multi_settings).post("/v1/pads", headers={"Authorization": f"Bearer {key}"}, json={"candidate_name": "Ada"})
    assert pad.status_code == 201
    assert pad.json()["join_url"].startswith(f"https://acme.{ROOT}/j/")


# Stripe ---------------------------------------------------------------------------------


def test_checkout_and_portal(billing_app, multi_settings, stripe):
    registry = billing_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    owner = tenant_client(billing_app, multi_settings, "acme", INTERVIEWER)

    response = owner.post("/api/workspace/checkout", json={"plan": "team", "interval": "annual"})
    assert response.json() == {"url": "https://checkout.stripe.test/cs_1"}
    path, form = stripe.calls[-1]
    assert path == "/v1/checkout/sessions"
    assert form["mode"] == ["subscription"]
    assert form["line_items[0][price]"] == ["price_ta"] and form["line_items[1][price]"] == ["price_ota"]
    assert form["client_reference_id"] == [tenant["id"]]
    assert form["customer_email"] == [INTERVIEWER["email"]]
    assert form["subscription_data[metadata][plan]"] == ["team"]
    assert "subscription_data[trial_end]" in form
    assert form["success_url"] == [f"https://acme.{ROOT}/#/workspace?billing=success"]

    assert owner.post("/api/workspace/portal").status_code == 409
    registry.platform.update_tenant(tenant["id"], stripe_customer_id="cus_1")
    registry.invalidate("acme")
    assert owner.post("/api/workspace/portal").json()["url"] == "https://billing.stripe.test/p/1"


def test_webhook_signature_is_required(billing_app, multi_settings):
    client = hub(billing_app, multi_settings)
    body, headers = signed({"id": "evt_1", "type": "invoice.paid", "data": {"object": {}}}, secret="wrong")
    assert client.post("/api/stripe/webhook", content=body, headers=headers).status_code == 400
    body, headers = signed({"id": "evt_1", "type": "invoice.paid", "data": {"object": {}}}, ts=int(time.time()) - 3600)
    assert client.post("/api/stripe/webhook", content=body, headers=headers).status_code == 400


def test_webhooks_activate_update_and_cancel_the_plan(billing_app, multi_settings):
    registry = billing_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    client = hub(billing_app, multi_settings)

    completed = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": tenant["id"], "customer": "cus_1", "subscription": "sub_1",
                            "metadata": {"tenant_id": tenant["id"], "plan": "team", "interval": "annual"}}},
    }
    body, headers = signed(completed)
    assert client.post("/api/stripe/webhook", content=body, headers=headers).json() == {"status": "applied"}
    assert client.post("/api/stripe/webhook", content=body, headers=headers).json() == {"status": "duplicate"}
    row = registry.platform.get_tenant("acme")
    assert (row["plan"], row["status"], row["stripe_customer_id"]) == ("team", "active", "cus_1")

    updated = {
        "id": "evt_2",
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "past_due", "metadata": {},
                            "items": {"data": [{"price": {"id": "price_sm"}, "current_period_start": 1_790_000_000,
                                                "current_period_end": 1_792_600_000}]}}},
    }
    body, headers = signed(updated)
    client.post("/api/stripe/webhook", content=body, headers=headers)
    row = registry.platform.get_tenant("acme")
    assert (row["plan"], row["billing_interval"], row["status"]) == ("starter", "monthly", "past_due")
    assert row["current_period_start"].startswith("2026-")

    deleted = {"id": "evt_3", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1", "customer": "cus_1"}}}
    body, headers = signed(deleted)
    client.post("/api/stripe/webhook", content=body, headers=headers)
    assert registry.platform.get_tenant("acme")["status"] == "canceled"


def test_paid_plans_report_usage_to_the_stripe_meter(billing_app, multi_settings, stripe):
    registry = billing_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    registry.platform.update_tenant(tenant["id"], plan="team", status="active", stripe_customer_id="cus_1")
    registry.invalidate("acme")
    owner = tenant_client(billing_app, multi_settings, "acme", INTERVIEWER)
    pin = owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "python"}).json()["pin"]
    assert tenant_client(billing_app, multi_settings, "acme").post("/api/join", json={"pin": pin}).status_code == 200

    path, form = stripe.calls[-1]
    assert path == "/v1/billing/meter_events"
    assert form["event_name"] == ["interview_credits"]
    assert form["payload[stripe_customer_id]"] == ["cus_1"] and form["payload[value]"] == ["1"]
    assert registry.platform.recent_usage(tenant["id"])[0]["reported_at"]
