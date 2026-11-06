"""Pydantic request and response models."""

from __future__ import annotations

import posixpath
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.languages import LanguageId

PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._\- ]+$")
MAX_PATH_LENGTH = 200
MAX_CONTENT_LENGTH = 500_000


def validate_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    if not path or path.startswith("/") or len(path) > MAX_PATH_LENGTH:
        raise ValueError("path must be a relative path up to 200 characters")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", "..") or not PATH_SEGMENT.match(part):
            raise ValueError(f"invalid path segment: {part!r}")
    return posixpath.normpath(path)


FilePath = Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH), AfterValidator(validate_path)]


class UserOut(BaseModel):
    name: str
    email: str
    picture: str | None = None


class LanguageOut(BaseModel):
    id: LanguageId
    label: str
    entry: str
    extensions: list[str]


class AppInfoOut(BaseModel):
    app_name: str
    tagline: str
    author: str
    google_enabled: bool
    storage: Literal["sqlite", "postgres"]


SessionMode = Literal["live", "take_home"]


class SessionCreate(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=80, description="Candidate's display name")
    language: LanguageId = Field(default=LanguageId.python, description="Starter language")
    org_id: str | None = Field(default=None, max_length=64, description="Organization that owns the session")


class SessionOut(BaseModel):
    id: str
    pin: str = Field(pattern=r"^\d{6}$")
    candidate_name: str
    interviewer_email: str
    language: LanguageId
    status: Literal["active", "ended"]
    created_at: str
    updated_at: str
    org_id: str | None = None
    mode: SessionMode = "live"
    title: str | None = None
    time_limit_minutes: int | None = None
    started_at: str | None = None
    submitted_at: str | None = None


class SessionPublic(BaseModel):
    id: str
    candidate_name: str
    language: LanguageId
    status: Literal["active", "ended"]
    mode: SessionMode = "live"
    title: str | None = None
    time_limit_minutes: int | None = None
    started_at: str | None = None
    submitted_at: str | None = None


class FileOut(BaseModel):
    id: str
    path: str
    content: str
    updated_at: str


class FileUpsert(BaseModel):
    path: FilePath
    content: str = Field(default="", max_length=MAX_CONTENT_LENGTH)


class FileRename(BaseModel):
    old_path: FilePath
    new_path: FilePath


class DeleteOut(BaseModel):
    deleted: list[str]


class RenameOut(BaseModel):
    renamed: dict[str, str]


class RunIn(BaseModel):
    entry_path: FilePath


class RunOut(BaseModel):
    id: int
    entry_path: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    created_at: str


class SessionDetail(SessionOut):
    files: list[FileOut]
    runs: list[RunOut]


class RecordingOut(BaseModel):
    id: str
    session_id: str
    created_by: str
    started_at: str
    ended_at: str | None = None
    event_count: int = 0


class SessionState(BaseModel):
    session: SessionPublic
    role: Literal["interviewer", "candidate"]
    pin: str | None = None
    files: list[FileOut]
    runs: list[RunOut]
    cursor: int
    recording: RecordingOut | None = None


class JoinIn(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$", description="6-digit session PIN")


class JoinOut(BaseModel):
    session: SessionPublic
    token: str = Field(description="Bearer token scoped to this session")


class EventOut(BaseModel):
    id: int
    kind: Literal["file_upsert", "file_delete", "file_rename", "run", "session", "recording"]
    path: str | None = None
    data: dict[str, Any] | None = None
    actor: str
    client_id: str | None = None
    created_at: str


class ChangesOut(BaseModel):
    cursor: int
    events: list[EventOut]


class PlaybackFile(BaseModel):
    path: str
    content: str


class RecordingPlayback(RecordingOut):
    session: SessionPublic
    initial_files: list[PlaybackFile]
    events: list[EventOut]


# Organizations ----------------------------------------------------------------

OrgRole = Literal["admin", "interviewer"]
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class OrgOut(BaseModel):
    id: str
    slug: str
    name: str
    created_by: str
    created_at: str
    role: OrgRole | None = None


class MemberIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    role: OrgRole = "interviewer"


class MemberOut(BaseModel):
    email: str
    role: OrgRole
    created_at: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_by: str
    created_at: str
    last_used_at: str | None = None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Shown once. Store it securely.")


class AshbySettings(BaseModel):
    interview_stage_ids: list[str] = Field(default_factory=list, description="Only create sessions for these stages; empty means all")
    default_language: LanguageId = LanguageId.python
    candidate_link_field_id: str | None = Field(default=None, max_length=64, description="Candidate custom field (Short answer) for the join link")
    post_link_note: bool = True
    push_results_note: bool = True
    upload_files: bool = True
    add_completed_assessment: bool = False


class AshbyConnect(BaseModel):
    api_key: str | None = Field(default=None, min_length=8, max_length=200, description="Omit to keep the stored key")
    settings: AshbySettings = Field(default_factory=AshbySettings)
    rotate_webhook_secret: bool = False


class ModernLoopSettings(BaseModel):
    job_stage_ids: list[str] = Field(default_factory=list, description="Only create sessions for these job stages; empty means all")
    default_language: LanguageId = LanguageId.python
    skip_ashby_candidates: bool = True


class ModernLoopConnect(BaseModel):
    api_token: str | None = Field(default=None, min_length=8, max_length=400, description="Omit to keep the stored token")
    settings: ModernLoopSettings = Field(default_factory=ModernLoopSettings)


class IntegrationOut(BaseModel):
    provider: Literal["ashby", "modernloop"]
    connected: bool
    enabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = None
    partner_base_url: str | None = None
    last_event_at: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None


class WebhookSecretOut(BaseModel):
    webhook_url: str
    webhook_secret: str


class OrgDetail(OrgOut):
    members: list[MemberOut]
    api_keys: list[ApiKeyOut]
    integrations: list[IntegrationOut]


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    language: LanguageId = LanguageId.python
    files: dict[str, str] | None = Field(default=None, description="Starter files; omit to use the language template")
    time_limit_minutes: int | None = Field(default=60, ge=5, le=24 * 60)


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str
    language: LanguageId
    files: dict[str, str]
    time_limit_minutes: int | None = None
    created_by: str
    created_at: str
    updated_at: str


class SyncResult(BaseModel):
    created: int
    ended: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


# Candidate join links and public API ----------------------------------------------


class JoinLinkIn(BaseModel):
    token: str = Field(min_length=10, max_length=600)


class PadCreate(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=80)
    candidate_email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    title: str | None = Field(default=None, max_length=200)
    language: LanguageId = LanguageId.python
    mode: SessionMode = "live"
    time_limit_minutes: int | None = Field(default=None, ge=5, le=24 * 60)
    interviewer_email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=254)
    files: dict[str, str] | None = Field(default=None, description="Starter files; omit to use the language template")


class ResultSummary(BaseModel):
    session_id: str
    candidate_name: str
    title: str | None = None
    language: LanguageId
    mode: SessionMode
    status: Literal["active", "ended"]
    created_at: str
    ended_at: str | None = None
    duration_minutes: float | None = None
    file_count: int
    run_count: int
    last_run_exit_code: int | None = None
    last_run_entry: str | None = None
    last_run_output: str | None = None
    interviewer_url: str
    playback_url: str | None = None
    files_url: str


class PadOut(BaseModel):
    id: str
    pin: str
    candidate_name: str
    title: str | None = None
    language: LanguageId
    mode: SessionMode
    status: Literal["active", "ended"]
    join_url: str
    interviewer_url: str
    created_at: str
    results: ResultSummary | None = None
