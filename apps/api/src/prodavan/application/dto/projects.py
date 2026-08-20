"""Project API DTOs."""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    display_name: str = Field(min_length=1)


class PatchProjectRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1)


class ProjectStats(BaseModel):
    runs_total: int = 0
    runs_active: int = 0
    inbox_pending: int = 0


class ProjectResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    status: str
    workspace_key: str
    storage_uri: str | None = None
    last_opened_at: datetime | None = None
    created_at: datetime
    stats: ProjectStats | None = None


class ProjectListResponse(BaseModel):
    cabinet_id: str
    items: list[ProjectResponse]
    next_cursor: str | None = None


class OpenProjectResponse(BaseModel):
    project_id: str
    workspace_key: str
    storage_paths: dict[str, str]
    opened_at: datetime
    access_token: str
    token_type: str = "bearer"


class ProjectStatsResponse(BaseModel):
    runs_by_phase: dict[str, int]
    inbox_files: int
    export_files: int
