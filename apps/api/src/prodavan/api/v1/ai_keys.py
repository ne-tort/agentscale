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
    provider: str = "cursor"
    api_kind: str = "cursor_sdk"
    secret: str | None = Field(default=None, min_length=1)
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    company_ids: list[str] = Field(default_factory=list)


class PatchKeyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    provider: str | None = None
    api_kind: str | None = None
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


@router.get("/audit-events")
async def list_audit_events(
    _admin: PlatformAdminDep,
    session: SessionDep,
    key_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    return await AiKeysService(session).list_audit_events(key_id=key_id, limit=limit)


@router.post("", status_code=201)
async def create_key(admin: PlatformAdminDep, session: SessionDep, body: CreateKeyBody) -> dict:
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
        principal=admin,
    )


@router.get("/{key_id}")
async def get_key(_admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    return await AiKeysService(session).get_key(key_id)


@router.patch("/{key_id}")
async def patch_key(admin: PlatformAdminDep, session: SessionDep, key_id: str, body: PatchKeyBody) -> dict:
    return await AiKeysService(session).patch_key(key_id, body.as_updates(), principal=admin)


@router.post("/{key_id}/renew")
async def renew_key(admin: PlatformAdminDep, session: SessionDep, key_id: str, body: RenewBody) -> dict:
    return await AiKeysService(session).renew(key_id, body.months, principal=admin)


@router.post("/{key_id}/rotate-secret")
async def rotate_secret(admin: PlatformAdminDep, session: SessionDep, key_id: str, body: RotateSecretBody) -> dict:
    return await AiKeysService(session).rotate_secret(key_id, body.secret, principal=admin)


@router.put("/{key_id}/companies")
async def set_companies(admin: PlatformAdminDep, session: SessionDep, key_id: str, body: CompaniesBody) -> dict:
    return await AiKeysService(session).set_companies(key_id, body.company_ids, principal=admin)


@router.delete("/{key_id}", status_code=204)
async def delete_key(admin: PlatformAdminDep, session: SessionDep, key_id: str) -> None:
    await AiKeysService(session).delete_key(key_id, principal=admin)


@router.post("/{key_id}/probe")
async def probe_key(admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    """Verify the key's secret works against the provider API (PROBE-P1).

    Performs a short HTTP request (GET /models, fallback: 1-token chat) and
    stores the last probe result. Never throws — returns a result dict with
    status ok/error/unavailable, latency_ms, models, etc.
    """
    from prodavan.application.ai_keys.probe.service import AiKeyProbeService

    return await AiKeyProbeService(session).probe_key(key_id, principal=admin)


@router.get("/{key_id}/probe")
async def get_last_probe(_admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    """Return the last stored probe result for a key (or {status: 'none'})."""
    from prodavan.application.ai_keys.probe.service import AiKeyProbeService

    result = await AiKeyProbeService(session).get_last_result(key_id)
    return result if result is not None else {"status": "none", "models": []}


class ProbeModelBody(BaseModel):
    model_config = {"extra": "forbid"}

    model: str = Field(min_length=1, max_length=200)


@router.post("/{key_id}/probe/model")
async def probe_key_model(
    admin: PlatformAdminDep,
    session: SessionDep,
    key_id: str,
    body: ProbeModelBody,
) -> dict:
    """Verify a specific model works with this key (1-token chat completion)."""
    from prodavan.application.ai_keys.probe.service import AiKeyProbeService

    return await AiKeyProbeService(session).probe_model(key_id, body.model, principal=admin)
