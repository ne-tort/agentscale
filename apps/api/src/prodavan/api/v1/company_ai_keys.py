"""Company-scoped AI Provider Keys — local CRUD + RO Admin-bound."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.identity.service import EntitlementService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}/ai-keys", tags=["company-ai-keys"])


class RenewBody(BaseModel):
    model_config = {"extra": "forbid"}

    months: int = Field(default=1, ge=1, le=24)


class RotateSecretBody(BaseModel):
    model_config = {"extra": "forbid"}

    secret: str = Field(min_length=1)


class CreateCompanyKeyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    provider: str = "cursor"
    api_kind: str = "cursor_sdk"
    secret: str | None = Field(default=None, min_length=1)
    next_renewal_at: datetime | None = None
    renewal_price: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    notes: str | None = None


class PatchCompanyKeyBody(BaseModel):
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


@router.get("")
async def list_company_keys(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> list[dict]:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiKeysService(session).list_keys_for_company(company_id)


@router.get("/{key_id}")
async def get_company_key(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiKeysService(session).get_key_for_company(key_id, company_id)


@router.post("", status_code=201)
async def create_company_key(
    company_id: str,
    body: CreateCompanyKeyBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiKeysService(session).create_key(
        name=body.name,
        provider=body.provider,
        api_kind=body.api_kind,
        secret=body.secret,
        next_renewal_at=body.next_renewal_at,
        renewal_price=body.renewal_price,
        currency=body.currency,
        notes=body.notes,
        owner_scope="company",
        owner_company_id=company_id,
        principal=principal,
    )


@router.patch("/{key_id}")
async def patch_company_key(
    company_id: str,
    key_id: str,
    body: PatchCompanyKeyBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    svc = AiKeysService(session)
    await svc.require_company_writable_key(key_id, company_id)
    return await svc.patch_key(key_id, body.as_updates(), principal=principal)


@router.delete("/{key_id}", status_code=204)
async def delete_company_key(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> None:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    svc = AiKeysService(session)
    await svc.require_company_writable_key(key_id, company_id)
    await svc.delete_key(key_id, principal=principal)


@router.post("/{key_id}/renew")
async def renew_company_key(
    company_id: str,
    key_id: str,
    body: RenewBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    svc = AiKeysService(session)
    await svc.require_company_writable_key(key_id, company_id)
    return await svc.renew(key_id, body.months, principal=principal)


@router.post("/{key_id}/rotate-secret")
async def rotate_company_key_secret(
    company_id: str,
    key_id: str,
    body: RotateSecretBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    svc = AiKeysService(session)
    await svc.require_company_writable_key(key_id, company_id)
    return await svc.rotate_secret(key_id, body.secret, principal=principal)


class KeyScopeBindingsBody(BaseModel):
    model_config = {"extra": "forbid"}

    employee_ids: list[str] = Field(default_factory=list)
    cabinet_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)


@router.get("/{key_id}/scope-bindings")
async def get_key_scope_bindings(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiKeysService(session).get_key_scope_bindings(key_id=key_id, company_id=company_id)


@router.put("/{key_id}/scope-bindings")
async def set_key_scope_bindings(
    company_id: str,
    key_id: str,
    body: KeyScopeBindingsBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiKeysService(session).set_key_scope_bindings(
        key_id=key_id,
        company_id=company_id,
        employee_ids=body.employee_ids,
        cabinet_ids=body.cabinet_ids,
        project_ids=body.project_ids,
    )


class KeyModelSelectionBody(BaseModel):
    model_config = {"extra": "forbid"}

    selections: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{key_id}/models")
async def list_key_models(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> list[dict]:
    from prodavan.application.ai_models.service import AiModelsService

    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiModelsService(session).list_key_models(company_id=company_id, key_id=key_id)


@router.put("/{key_id}/models")
async def update_key_models(
    company_id: str,
    key_id: str,
    body: KeyModelSelectionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> list[dict]:
    from prodavan.application.ai_models.service import AiModelsService

    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    out = await AiModelsService(session).update_key_models(
        company_id=company_id,
        key_id=key_id,
        selections=body.selections,
    )
    await session.commit()
    return out


@router.get("/{key_id}/models/live")
async def list_key_models_live(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    from prodavan.application.ai_models.live_service import AiModelsLiveService

    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiModelsLiveService(session).list_live_for_key(company_id=company_id, key_id=key_id)


@router.post("/{key_id}/probe")
async def probe_company_key(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    """Verify the key's secret works against the provider API (PROBE-P1).

    Performs a short HTTP request (GET /models, fallback: 1-token chat) and
    stores the last probe result. Never throws — returns a result dict with
    status ok/error/unavailable, latency_ms, models, etc.
    """
    from prodavan.application.ai_keys.probe.service import AiKeyProbeService

    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    # Only company-writable keys can be probed (platform-bound RO keys still
    # probeable — the secret is readable by platform admin path; for company
    # actor we require visibility only).
    await AiKeysService(session).require_company_key_visible(key_id, company_id)
    return await AiKeyProbeService(session).probe_key(key_id, principal=principal)


@router.get("/{key_id}/probe")
async def get_company_last_probe(
    company_id: str,
    key_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    """Return the last stored probe result for a key (or {status: 'none'})."""
    from prodavan.application.ai_keys.probe.service import AiKeyProbeService

    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    await AiKeysService(session).require_company_key_visible(key_id, company_id)
    result = await AiKeyProbeService(session).get_last_result(key_id)
    return result if result is not None else {"status": "none", "models": []}
