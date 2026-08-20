"""Prompt API DTOs."""

from datetime import datetime

from pydantic import BaseModel, Field


class PromptTreeResponse(BaseModel):
    root: str
    tree: list[dict]
    current_version: str | None = None


class PromptFileResponse(BaseModel):
    path: str
    content: str
    sha256: str
    updated_at: datetime
    etag: str


class PutPromptFileRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str


class CreatePromptVersionRequest(BaseModel):
    label: str | None = None


class PromptFileManifest(BaseModel):
    path: str
    sha256: str
    size_bytes: int


class PromptVersionResponse(BaseModel):
    version_id: str
    cabinet_id: str
    label: str | None
    parent_version_id: str | None
    created_at: datetime
    created_by: str
    files_count: int
    files: list[PromptFileManifest] | None = None


class PromptVersionListResponse(BaseModel):
    items: list[PromptVersionResponse]


class RollbackPromptVersionRequest(BaseModel):
    confirm: bool = True
    create_backup_version: bool = True
