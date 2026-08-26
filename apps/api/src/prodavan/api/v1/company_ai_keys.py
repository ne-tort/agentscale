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
