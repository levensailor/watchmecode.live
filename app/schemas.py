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


class SessionCreate(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=80, description="Candidate's display name")
    language: LanguageId = Field(default=LanguageId.python, description="Starter language")


class SessionOut(BaseModel):
    id: str
    pin: str = Field(pattern=r"^\d{6}$")
    candidate_name: str
    interviewer_email: str
    language: LanguageId
    status: Literal["active", "ended"]
    created_at: str
    updated_at: str


class SessionPublic(BaseModel):
    id: str
    candidate_name: str
    language: LanguageId
    status: Literal["active", "ended"]


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


class SessionState(BaseModel):
    session: SessionPublic
    role: Literal["interviewer", "candidate"]
    pin: str | None = None
    files: list[FileOut]
    runs: list[RunOut]
    cursor: int


class JoinIn(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$", description="6-digit session PIN")


class JoinOut(BaseModel):
    session: SessionPublic
    token: str = Field(description="Bearer token scoped to this session")


class EventOut(BaseModel):
    id: int
    kind: Literal["file_upsert", "file_delete", "file_rename", "run", "session"]
    path: str | None = None
    data: dict[str, Any] | None = None
    actor: str
    client_id: str | None = None
    created_at: str


class ChangesOut(BaseModel):
    cursor: int
    events: list[EventOut]
