"""Admin AI Provider Keys HTTP surface (L03) — no raw secret in responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.ai_keys.endpoint import normalize_auth_scheme, normalize_base_url
from prodavan.application.ai_keys.service import AiKeysService

router = APIRouter(prefix="/admin/ai-keys", tags=["ai-keys"])


class CreateKeyBody(BaseModel):
    """Create body: an endpoint (`base_url` + `secret`) *or* the legacy
    provider + api_kind (+ catalog_entry_id) selection.

    The provider dropdown is being removed from the UI: the administrator types
    an endpoint URL and an API key. The endpoint is matched to (or created as)
    an `ai.http_providers` catalog entry and the key links to it; provider and
    api_kind then come from that entry. A body without `base_url` behaves
    exactly as before (catalog provider/api_kind, cursor/cursor_sdk defaults).
    """

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    provider: str | None = None
    api_kind: str | None = None
    secret: str | None = Field(default=None, min_length=1)
    catalog_entry_id: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(
        default=None,
        max_length=2000,
        description="Absolute http(s) endpoint, e.g. https://api.example.com/v1",
        examples=["https://api.example.com/v1"],
    )
    models_path: str | None = Field(
        default=None,
        max_length=500,
        description="Models listing path relative to base_url (default /models)",
    )
    chat_completions_path: str | None = Field(
        default=None,
        max_length=500,
        description="Chat path relative to base_url (default /chat/completions)",
    )
    auth_scheme: str | None = Field(
        default=None,
        max_length=32,
        description="bearer (default) | x-api-key",
    )
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    company_ids: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: str | None) -> str | None:
        """Reject a non-http(s) endpoint here: 422 AppError shape, not a 500."""
        return normalize_base_url(v) if v is not None else None

    @field_validator("auth_scheme")
    @classmethod
    def _check_auth_scheme(cls, v: str | None) -> str | None:
        return normalize_auth_scheme(v) if v is not None else None


class PatchKeyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    provider: str | None = None
    api_kind: str | None = None
    catalog_entry_id: str | None = Field(default=None, max_length=64)
    # Re-point the key at an endpoint. models_path / chat_completions_path /
    # auth_scheme live on the shared catalog entry, so they only apply together
    # with base_url.
    base_url: str | None = Field(default=None, max_length=2000)
    models_path: str | None = Field(default=None, max_length=500)
    chat_completions_path: str | None = Field(default=None, max_length=500)
    auth_scheme: str | None = Field(default=None, max_length=32)
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = None
    notes: str | None = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: str | None) -> str | None:
        return normalize_base_url(v) if v is not None else None

    @field_validator("auth_scheme")
    @classmethod
    def _check_auth_scheme(cls, v: str | None) -> str | None:
        return normalize_auth_scheme(v) if v is not None else None

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
    """Create a key from an endpoint (`base_url` + `secret`) or, as before,
    from a catalog provider/api_kind."""
    return await AiKeysService(session).create_key(
        name=body.name,
        provider=body.provider,
        api_kind=body.api_kind,
        secret=body.secret,
        catalog_entry_id=body.catalog_entry_id,
        base_url=body.base_url,
        models_path=body.models_path,
        chat_completions_path=body.chat_completions_path,
        auth_scheme=body.auth_scheme,
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


class AdminKeyModelSelectionBody(BaseModel):
    model_config = {"extra": "forbid"}

    selections: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{key_id}/models")
async def admin_list_key_models(
    _admin: PlatformAdminDep,
    session: SessionDep,
    key_id: str,
) -> list[dict]:
    """Admin: list models visible to a key (platform + company scope) with
    the key↔model binding state (enabled / is_default).
    """
    from prodavan.application.ai_models.service import AiModelsService

    return await AiModelsService(session).list_key_models_admin(key_id=key_id)


@router.put("/{key_id}/models")
async def admin_update_key_models(
    admin: PlatformAdminDep,
    session: SessionDep,
    key_id: str,
    body: AdminKeyModelSelectionBody,
) -> list[dict]:
    """Admin: set the key↔model binding state (enabled / is_default toggle)."""
    from prodavan.application.ai_models.service import AiModelsService

    out = await AiModelsService(session).update_key_models_admin(
        key_id=key_id,
        selections=body.selections,
    )
    await session.commit()
    return out


# ---------------------------------------------------------------------------
# xAI (Grok) OAuth — device-code flow (admin-контур платформенных ключей).
# ---------------------------------------------------------------------------


@router.post("/{key_id}/oauth/xai/device")
async def start_key_xai_device(admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    from prodavan.application.ai_keys.oauth.xai_oauth import XaiOAuthService

    return await XaiOAuthService(session).start_device_flow(key_id, principal=admin)


@router.get("/{key_id}/oauth/xai/device")
async def get_key_xai_device(admin: PlatformAdminDep, session: SessionDep, key_id: str) -> dict:
    from prodavan.application.ai_keys.oauth.xai_oauth import XaiOAuthService

    return await XaiOAuthService(session).device_flow_status(key_id, principal=admin)


@router.delete("/{key_id}/oauth/xai/device", status_code=204)
async def cancel_key_xai_device(admin: PlatformAdminDep, session: SessionDep, key_id: str) -> None:
    from prodavan.application.ai_keys.oauth.xai_oauth import XaiOAuthService

    await XaiOAuthService(session).cancel_device_flow(key_id)
