"""Admin AI Provider Keys HTTP surface (L03) — no raw secret in responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.ai_keys.service import AiKeysService

router = APIRouter(prefix="/admin/ai-keys", tags=["ai-keys"])


class CreateKeyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    provider: str
    api_kind: str
    secret: str = Field(min_length=1)
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    company_ids: list[str] = Field(default_factory=list)


class PatchKeyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = None
    notes: str | None = None

    def as_updates(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class RenewBody(BaseModel):
    model_config = {"extra": "forbid"}

    months: int = Field(ge=1, le=12)


class RotateSecretBody(BaseModel):
    model_config = {"extra": "forbid"}

    secret: str = Field(min_length=1)


class CompaniesBody(BaseModel):
    model_config = {"extra": "forbid"}

    company_ids: list[str]

    @field_validator("company_ids")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


@router.get("")
async def list_keys(_admin: PlatformAdminDep, session: SessionDep) -> list[dict]:
    return await AiKeysService(session).list_keys()


@router.post("", status_code=201)
async def create_key(_admin: PlatformAdminDep, session: SessionDep, body: CreateKeyBody) -> dict:
    return await AiKeysService(session).create_key(
        name=body.name,
        provider=body.provider,
        api_kind=body.api_kind,
        secret=body.secret,
        next_renewal_at=body.next_renewal_at,
        renewal_price=body.renewal_price,
        currency=body.currency,
        notes=body.notes,
        company_ids=body.company_ids,
    )


@router.get("/{key_id}")
async def get_key(_admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    return await AiKeysService(session).get_key(key_id)


@router.patch("/{key_id}")
async def patch_key(_admin: PlatformAdminDep, session: SessionDep, key_id: str, body: PatchKeyBody) -> dict:
    return await AiKeysService(session).patch_key(key_id, body.as_updates())


@router.post("/{key_id}/renew")
async def renew_key(_admin: PlatformAdminDep, session: SessionDep, key_id: str, body: RenewBody) -> dict:
    return await AiKeysService(session).renew(key_id, body.months)


@router.post("/{key_id}/rotate-secret")
async def rotate_secret(_admin: PlatformAdminDep, session: SessionDep, key_id: str, body: RotateSecretBody) -> dict:
    return await AiKeysService(session).rotate_secret(key_id, body.secret)


@router.put("/{key_id}/companies")
async def set_companies(_admin: PlatformAdminDep, session: SessionDep, key_id: str, body: CompaniesBody) -> dict:
    return await AiKeysService(session).set_companies(key_id, body.company_ids)


@router.delete("/{key_id}", status_code=204)
async def delete_key(_admin: PlatformAdminDep, session: SessionDep, key_id: str) -> None:
    await AiKeysService(session).delete_key(key_id)
