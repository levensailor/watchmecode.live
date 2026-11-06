"""In-memory fakes for the Ashby and ModernLoop APIs, served through httpx.MockTransport."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

APPLICATION_ID = "app-1"
CANDIDATE_ID = "cand-1"


class FakeApis:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: set[str] = set()
        self.application = {
            "id": APPLICATION_ID,
            "candidate": {"id": CANDIDATE_ID, "name": "Ada Lovelace", "primaryEmailAddress": {"value": "ada@example.com"}},
            "job": {"id": "job-1", "title": "Backend Engineer"},
        }
        self.ashby_users = {"panel@example.com": "ashby-user-1"}
        self.ml_events: list[dict[str, Any]] = []
        self.ml_candidates: dict[str, dict[str, Any]] = {}
        self.ml_users: dict[str, dict[str, Any]] = {}
        self.emails: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def named(self, endpoint: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["endpoint"] == endpoint]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.ashbyhq.com":
            return self._ashby(request)
        if host == "api.modernloop.io":
            return self._modernloop(request)
        if host == "api.resend.com":
            self.emails.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "email-1"})
        return httpx.Response(404, json={"error": "unknown host"})

    def _ashby(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.lstrip("/")
        auth = request.headers.get("authorization", "")
        key = base64.b64decode(auth.split(" ", 1)[1]).decode().split(":")[0] if auth.startswith("Basic ") else ""
        call: dict[str, Any] = {"endpoint": endpoint, "key": key, "on_behalf_of": request.headers.get("x-on-behalf-of")}
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            call["multipart"] = request.content
        else:
            call["body"] = json.loads(request.content or b"{}")
        self.calls.append(call)
        if endpoint in self.fail:
            return httpx.Response(200, json={"success": False, "errors": [f"{endpoint} failed"]})
        if endpoint == "application.info":
            return httpx.Response(200, json={"success": True, "results": self.application})
        if endpoint == "user.search":
            user_id = self.ashby_users.get(call["body"].get("email"))
            return httpx.Response(200, json={"success": True, "results": [{"id": user_id}] if user_id else []})
        if endpoint == "apiKey.info":
            return httpx.Response(200, json={"success": True, "results": {"id": "key-1", "title": "watchmecode"}})
        return httpx.Response(200, json={"success": True, "results": {"id": "ok"}})

    def _modernloop(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append({"endpoint": f"ml:{path}", "params": dict(request.url.params), "auth": request.headers.get("authorization")})
        if path == "/v1/me":
            return httpx.Response(200, json={"id": "u", "email": "bot@modernloop.example", "organization": {"name": "Acme"}})
        if path == "/v1/interview-events":
            return httpx.Response(200, json={"data": self.ml_events, "pagination": {"next_cursor": None}})
        if path.startswith("/v1/candidates/"):
            return httpx.Response(200, json=self.ml_candidates[path.rsplit("/", 1)[1]])
        if path.startswith("/v1/users/"):
            user = self.ml_users.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=user) if user else httpx.Response(404, json={})
        return httpx.Response(404, json={})


def schedule_payload(action: str, events: list[dict[str, Any]], status: str = "Scheduled", stage: str = "stage-1") -> dict[str, Any]:
    return {
        "action": action,
        "data": {
            "interviewSchedule": {
                "id": "sched-1",
                "status": status,
                "applicationId": APPLICATION_ID,
                "interviewStageId": stage,
                "interviewEvents": events,
            }
        },
    }


def event(event_id: str, emails: list[str]) -> dict[str, Any]:
    return {
        "id": event_id,
        "interviewId": "int-1",
        "interviewScheduleId": "sched-1",
        "startTime": "2026-11-10T15:00:00Z",
        "endTime": "2026-11-10T16:00:00Z",
        "feedbackLink": "https://app.ashbyhq.com/feedback",
        "hasSubmittedFeedback": False,
        "interviewers": [
            {"id": f"u-{i}", "email": email, "firstName": "Pan", "lastName": "El", "trainingRole": None, "interviewerPool": None, "isFeedbackRequired": True}
            for i, email in enumerate(emails)
        ],
    }
