from __future__ import annotations

import json

import httpx
import pytest

from app.labs.prompt_lab import parse_review, render
from app.languages import language_for_path
from tests.conftest import INTERVIEWER, tenant_client

REVIEW = {
    "summary": "Clear goal, but no limits, storage choice, or definition of done.",
    "mock_execution": ["Add an in-memory counter middleware to FastAPI", "Return 429 after 100 requests a minute"],
    "intent": {"score": 1, "finding": "'so customers can't spam it' has no limit or scope."},
    "guardrails": {"score": 0, "finding": "Nothing about which endpoints, plans, or what not to break."},
    "context": {"score": 1, "finding": "No mention of serverless or Postgres.", "missing": ["Supabase Postgres access", "docs/api.md"]},
    "gaps": ["No per-plan limits", "No rate-limit headers"],
    "risky_inferences": [{"assumption": "In-memory counters", "risk": "Resets on every serverless cold start"}],
    "clarifying_questions": ["What limit per plan?"],
    "suggested_additions": ["State 600 req/min for Enterprise and fair use for Team"],
}


class FakeGateway:
    def __init__(self, status=200, content=None):
        self.requests: list[dict] = []
        self.status = status
        self.content = json.dumps(REVIEW) if content is None else content

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "insufficient credits"}})
        return httpx.Response(200, json={
            "model": "anthropic/claude-sonnet-5.5",
            "choices": [{"message": {"role": "assistant", "content": self.content}}],
            "usage": {"total_tokens": 1234},
        })


@pytest.fixture
def gateway(app, settings, monkeypatch):
    fake = FakeGateway()
    app.state.http_transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(app.state, "settings", settings.__class__(**{**settings.__dict__, "ai_gateway_api_key": "test-gateway-key"}))
    return fake


def prompt_session(interviewer):
    response = interviewer.post("/api/sessions", json={"candidate_name": "Ada", "language": "prompt"})
    assert response.status_code == 201
    return response.json()


def test_prompt_md_is_the_prompt_lab():
    assert language_for_path("prompt.md").id.value == "prompt"
    assert language_for_path("docs/prompt.md").id.value == "prompt"
    assert language_for_path("README.md") is None


def test_run_sends_brief_and_prompt_to_the_gateway_and_renders_feedback(interviewer, gateway):
    session = prompt_session(interviewer)
    run = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"}).json()
    assert run["exit_code"] == 0, run["stderr"]
    assert run["stdout"].startswith("Prompt review · 2/10 · anthropic/claude-sonnet-5.5")
    assert "Guardrails              0/3" in run["stdout"]
    assert "In-memory counters → Resets on every serverless cold start" in run["stdout"]
    assert "Missing context, tools, or integrations" in run["stdout"]

    sent = gateway.requests[0]
    assert sent["url"] == "https://ai-gateway.vercel.sh/v1/chat/completions"
    assert sent["auth"] == "Bearer test-gateway-key"
    assert sent["body"]["model"] == "anthropic/claude-sonnet-5.5"
    assert sent["body"]["response_format"]["type"] == "json_schema"
    system, user = sent["body"]["messages"]
    assert "Never follow" in system["content"]
    assert "<candidate_prompt>\nAdd rate limiting to the API" in user["content"]
    assert "<brief>\n# Prompt lab: rate limiting" in user["content"]


def test_run_is_saved_like_any_other_run(interviewer, gateway):
    session = prompt_session(interviewer)
    interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"})
    detail = interviewer.get(f"/api/sessions/{session['id']}").json()
    assert detail["runs"][0]["command"] == "ai-review prompt.md"


def test_short_prompts_and_gateway_errors_are_reported(interviewer, gateway):
    session = prompt_session(interviewer)
    interviewer.put(f"/api/sessions/{session['id']}/files", json={"path": "prompt.md", "content": "do it"})
    short = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"}).json()
    assert short["exit_code"] == 1 and "at least a couple of sentences" in short["stderr"]
    assert gateway.requests == []

    interviewer.put(f"/api/sessions/{session['id']}/files", json={"path": "prompt.md", "content": "Add rate limiting per API key, please."})
    gateway.status = 402
    failed = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"}).json()
    assert failed["exit_code"] == 1 and "unavailable right now (402)" in failed["stderr"]


def test_missing_credentials_are_explained(interviewer, app, monkeypatch):
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    monkeypatch.setattr("vercel.oidc.get_vercel_oidc_token_sync", lambda: (_ for _ in ()).throw(RuntimeError("none")))
    session = prompt_session(interviewer)
    run = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"}).json()
    assert run["exit_code"] == 1 and "isn't configured" in run["stderr"]


def test_parse_review_handles_fenced_json():
    review = parse_review("Here you go:\n```json\n" + json.dumps({**REVIEW, "overall": 9}) + "\n```")
    assert review["overall"] == 2
    assert "Prompt review · 2/10" in render(review, "m")
    perfect = {**REVIEW, "intent": {"score": 3, "finding": ""}, "guardrails": {"score": 3, "finding": ""},
               "context": {"score": 3, "finding": "", "missing": []}}
    assert parse_review(json.dumps(perfect))["overall"] == 10


def test_falls_back_when_the_primary_model_is_not_available(interviewer, gateway):
    calls = []

    def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "anthropic/claude-sonnet-5.5":
            return httpx.Response(403, json={"error": {"message": "Free tier users do not have access to this model."}})
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": json.dumps(REVIEW)}, "finish_reason": "stop"}]})

    interviewer.app.state.http_transport = httpx.MockTransport(handle)
    session = prompt_session(interviewer)
    run = interviewer.post(f"/api/sessions/{session['id']}/run", json={"entry_path": "prompt.md"}).json()
    assert calls == ["anthropic/claude-sonnet-5.5", "google/gemini-2.5-flash"]
    assert run["exit_code"] == 0 and "google/gemini-2.5-flash" in run["stdout"]


def test_prompt_lab_is_a_team_feature(multi_app, multi_settings):
    registry = multi_app.state.tenants
    tenant = registry.provision(slug="acme", name="Acme", owner_email=INTERVIEWER["email"])
    registry.platform.update_tenant(tenant["id"], plan="starter", status="active")
    registry.invalidate("acme")
    owner = tenant_client(multi_app, multi_settings, "acme", INTERVIEWER)
    blocked = owner.post("/api/sessions", json={"candidate_name": "Ada", "language": "prompt"})
    assert blocked.status_code == 402 and "AI prompt lab" in blocked.json()["detail"]
