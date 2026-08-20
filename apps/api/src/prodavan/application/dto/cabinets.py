"""Cabinet API DTOs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateCabinetRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    display_name: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)


class PatchCabinetRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1)


class CabinetResponse(BaseModel):
    id: UUID
    slug: str
    display_name: str
    profile_id: str | None
    status: str
    capabilities: dict
    storage_uri: str | None = None
    created_at: datetime


class CabinetListResponse(BaseModel):
    items: list[CabinetResponse]
    next_cursor: str | None = None


class ProfileResponse(BaseModel):
    id: str
    version: str
    display_name: str
    description: str | None
    deprecated: bool
    capabilities_preview: dict


class ProfileListResponse(BaseModel):
    items: list[ProfileResponse]


class SwitchCabinetResponse(BaseModel):
    cabinet_id: UUID
    tenant_id: UUID
    workspace_key: str
    capabilities: dict
    access_token: str
    token_type: str = "bearer"


class ManifestResponse(BaseModel):
    cabinet_id: UUID
    profile_id: str | None
    ui: dict
    capabilities: dict
