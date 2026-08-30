"""Request bodies for workspace filesystem routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceMoveBody(BaseModel):
    model_config = {"extra": "forbid"}

    src: str = Field(min_length=1, max_length=500)
    dst: str = Field(min_length=1, max_length=500)


class WorkspaceCopyBody(BaseModel):
    model_config = {"extra": "forbid"}

    src: str = Field(min_length=1, max_length=500)
    dst: str = Field(min_length=1, max_length=500)
