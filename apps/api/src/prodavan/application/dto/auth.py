"""Request/response DTOs for auth."""

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1)
    tenant_slug: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    tenant_display_name: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str


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
