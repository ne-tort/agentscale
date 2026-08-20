"""Request/response DTOs for auth."""

from uuid import UUID

from pydantic import BaseModel, Field

from prodavan.application.dto.validators import OptionalEmail


class LoginRequest(BaseModel):
    login_id: str = Field(min_length=2, max_length=64)
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UpdateProfileRequest(BaseModel):
    contact_person: str | None = None
    phone: str | None = None
    email: OptionalEmail = None


class UserResponse(BaseModel):
    id: UUID
    login_id: str
    company_name: str
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    role: str
    status: str
    # Compat for older clients
    display_name: str | None = None


class TenantResponse(BaseModel):
    id: UUID
    slug: str
    display_name: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    tenants: list[TenantResponse]


class MeResponse(BaseModel):
    user: UserResponse
    tenant: TenantResponse
    cabinet_ids: list[UUID]
