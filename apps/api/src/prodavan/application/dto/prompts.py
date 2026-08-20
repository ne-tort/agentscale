"""Prompt API DTOs."""

from datetime import datetime

from pydantic import BaseModel


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
