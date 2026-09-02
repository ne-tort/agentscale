"""Company AI model catalog API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.ai_models.service import AiModelsService
from prodavan.application.identity.service import EntitlementService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}/ai-models", tags=["company-ai-models"])


class CreateModelBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=128)
    api_kinds: list[str] = Field(default_factory=list)
    input_price_usd_per_mtok: Decimal | None = None
    output_price_usd_per_mtok: Decimal | None = None
    max_context_tokens: int | None = Field(default=None, ge=1)
    publisher: str | None = Field(default=None, max_length=128)
    released_at: date | None = None


class PatchModelBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_kinds: list[str] | None = None
    input_price_usd_per_mtok: Decimal | None = None
    output_price_usd_per_mtok: Decimal | None = None
    max_context_tokens: int | None = Field(default=None, ge=1)
    publisher: str | None = Field(default=None, max_length=128)
    released_at: date | None = None


class KeyModelSelectionBody(BaseModel):
    model_config = {"extra": "forbid"}

    selections: list[dict[str, Any]] = Field(default_factory=list)


@router.get("")
async def list_models(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> list[dict]:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AiModelsService(session).list_models_for_company(company_id)


@router.post("")
async def create_model(
    company_id: str,
    body: CreateModelBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    out = await AiModelsService(session).create_model(
        company_id=company_id,
        name=body.name,
        api_kinds=body.api_kinds,
        input_price_usd_per_mtok=body.input_price_usd_per_mtok,
        output_price_usd_per_mtok=body.output_price_usd_per_mtok,
        max_context_tokens=body.max_context_tokens,
        publisher=body.publisher,
        released_at=body.released_at,
    )
    await session.commit()
    return out


@router.patch("/{model_id}")
async def patch_model(
    company_id: str,
    model_id: str,
    body: PatchModelBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    out = await AiModelsService(session).update_model(
        company_id=company_id,
        model_id=model_id,
        name=body.name,
        api_kinds=body.api_kinds,
        input_price_usd_per_mtok=body.input_price_usd_per_mtok,
        output_price_usd_per_mtok=body.output_price_usd_per_mtok,
        max_context_tokens=body.max_context_tokens,
        publisher=body.publisher,
        released_at=body.released_at,
    )
    await session.commit()
    return out
