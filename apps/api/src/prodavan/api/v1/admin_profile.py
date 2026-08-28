"""Platform Admin profile HTTP — login + password."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.admin.profile_service import AdminProfileService

router = APIRouter(prefix="/admin/profile", tags=["admin-profile"])


class AdminLoginBody(BaseModel):
    model_config = {"extra": "forbid"}

    login: str = Field(min_length=3, max_length=64)


class AdminPasswordBody(BaseModel):
    model_config = {"extra": "forbid"}

    password: str = Field(min_length=8, max_length=200)


@router.get("")
async def get_admin_profile(principal: PlatformAdminDep, _: SessionDep) -> dict:
    return await AdminProfileService().get_profile(principal)


@router.put("/login")
async def set_admin_login(
    body: AdminLoginBody,
    principal: PlatformAdminDep,
    _: SessionDep,
) -> dict:
    return await AdminProfileService().set_login(principal, login=body.login)


@router.put("/password")
async def set_admin_password(
    body: AdminPasswordBody,
    principal: PlatformAdminDep,
    _: SessionDep,
) -> dict:
    return await AdminProfileService().set_password(principal, password=body.password)
