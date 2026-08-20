"""Admin API DTOs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from prodavan.application.dto.validators import OptionalEmail


class AdminCreateUserRequest(BaseModel):
    login_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    company_name: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=8)
    contact_person: str | None = None
    phone: str | None = None
    email: OptionalEmail = None


class AdminUpdateUserRequest(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(active|suspended)$")
    contact_person: str | None = None
    phone: str | None = None
    email: OptionalEmail = None
    password: str | None = Field(default=None, min_length=8)


class AdminUserResponse(BaseModel):
    id: UUID
    login_id: str
    company_name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    role: str
    status: str
    tenant_id: UUID | None
    tenant_slug: str | None
    created_at: datetime
    deleted_at: datetime | None = None


class AdminStatsResponse(BaseModel):
    users_total: int
    users_by_status: dict[str, int]
    tenants_total: int
    cabinets_total: int
    projects_total: int
    runs_total: int
