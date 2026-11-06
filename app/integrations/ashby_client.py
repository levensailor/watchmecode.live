"""Minimal Ashby public API client (HTTP Basic, API key as username)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.integrations.http import IntegrationError, make_client

logger = logging.getLogger(__name__)

ACCEPT = "application/json; version=1"


class AshbyClient:
    def __init__(self, settings: Settings, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._client = make_client(
            settings,
            transport,
            base_url=settings.ashby_api_base,
            auth=(api_key, ""),
            headers={"Accept": ACCEPT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AshbyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, endpoint: str, payload: dict[str, Any] | None = None, on_behalf_of: str | None = None) -> Any:
        headers = {"X-On-Behalf-Of": on_behalf_of} if on_behalf_of else {}
        try:
            response = self._client.post(f"/{endpoint}", json=payload or {}, headers=headers)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Ashby {endpoint} request failed: {exc}") from exc
        return self._unwrap(endpoint, response)

    @staticmethod
    def _unwrap(endpoint: str, response: httpx.Response) -> Any:
        if response.status_code in (401, 403):
            raise IntegrationError(f"Ashby rejected the API key for {endpoint} (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError as exc:
            raise IntegrationError(f"Ashby {endpoint} returned HTTP {response.status_code}") from exc
        if response.status_code >= 400 or not body.get("success", False):
            errors = body.get("errors") or [body.get("errorInfo", {}).get("message", "unknown error")]
            raise IntegrationError(f"Ashby {endpoint} failed: {'; '.join(str(e) for e in errors)}")
        return body.get("results")

    # Reads -----------------------------------------------------------------

    def api_key_info(self) -> dict[str, Any]:
        return self.call("apiKey.info")

    def application_info(self, application_id: str) -> dict[str, Any]:
        return self.call("application.info", {"applicationId": application_id})

    def find_user_id(self, email: str) -> str | None:
        try:
            users = self.call("user.search", {"email": email}) or []
        except IntegrationError as exc:
            logger.info("Ashby user.search failed for an interviewer: %s", exc)
            return None
        return users[0]["id"] if users else None

    # Writes ----------------------------------------------------------------

    def create_note(self, candidate_id: str, html: str, on_behalf_of: str | None = None) -> dict[str, Any]:
        return self.call(
            "candidate.createNote",
            {"candidateId": candidate_id, "note": {"type": "text/html", "value": html}, "sendNotifications": False},
            on_behalf_of=on_behalf_of,
        )

    def set_candidate_field(self, candidate_id: str, field_id: str, value: str) -> dict[str, Any]:
        return self.call(
            "customField.setValue",
            {"objectType": "Candidate", "objectId": candidate_id, "fieldId": field_id, "fieldValue": value},
        )

    def upload_file(self, candidate_id: str, filename: str, content: bytes, content_type: str = "application/zip") -> Any:
        try:
            response = self._client.post(
                "/candidate.uploadFile",
                data={"candidateId": candidate_id},
                files={"file": (filename, content, content_type)},
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Ashby candidate.uploadFile request failed: {exc}") from exc
        return self._unwrap("candidate.uploadFile", response)

    def assessment_update(self, payload: dict[str, Any]) -> Any:
        return self.call("assessment.update", payload)

    def add_completed_assessment(self, payload: dict[str, Any]) -> Any:
        return self.call("assessment.addCompletedToCandidate", payload)
