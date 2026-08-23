"""Platform Admin company control plane HTTP (L04)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, PrincipalDep, SessionDep, get_current_employee
from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.identity.service import EntitlementService
from prodavan.domain.admin import CompanyAgentRuntimePolicy, CompanyCabinetQuota
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/admin/companies", tags=["admin-companies"])


class CabinetQuotaBody(BaseModel):
    model_config = {"extra": "forbid"}

    max_cabinets: int = Field(ge=1, le=10_000)
    max_packages_per_cabinet: int = Field(ge=0, le=500)
    max_bundle_import_mb: int = Field(ge=1, le=10_000)


class AgentPolicyBody(BaseModel):
    model_config = {"extra": "forbid"}

    tool_preset: str = "workspace_dev"
    preferred_provider: str | None = None
    platform_fallback: bool = True
    model_allowlist: list[str] = Field(default_factory=list)
    max_agent_tokens_month: int | None = Field(default=None, ge=1)
    max_tokens_per_run: int | None = Field(default=None, ge=1)


@router.get("")
async def list_companies(_: PlatformAdminDep, session: SessionDep) -> dict:
    items = await AdminCompanyService(session).list_companies()
    return {"items": items}


@router.get("/{company_id}")
async def get_company(company_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await AdminCompanyService(session).get_company(company_id)


@router.put("/{company_id}/cabinet-quotas")
async def set_cabinet_quotas(
    company_id: str,
    body: CabinetQuotaBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    quota = CompanyCabinetQuota(
        max_cabinets=body.max_cabinets,
        max_packages_per_cabinet=body.max_packages_per_cabinet,
        max_bundle_import_mb=body.max_bundle_import_mb,
    )
    return await AdminCompanyService(session).set_cabinet_quotas(company_id, quota)


@router.get("/{company_id}/agent-policy")
async def get_agent_policy(company_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    policy = await AdminCompanyService(session).get_agent_policy(company_id)
    return {
        "tool_preset": policy.tool_preset,
        "preferred_provider": policy.preferred_provider,
        "platform_fallback": policy.platform_fallback,
        "model_allowlist": policy.model_allowlist,
        "max_agent_tokens_month": policy.max_agent_tokens_month,
        "max_tokens_per_run": policy.max_tokens_per_run,
    }


@router.put("/{company_id}/agent-policy")
async def set_agent_policy(
    company_id: str,
    body: AgentPolicyBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    policy = CompanyAgentRuntimePolicy(
        tool_preset=body.tool_preset,
        preferred_provider=body.preferred_provider,
        platform_fallback=body.platform_fallback,
        model_allowlist=body.model_allowlist,
        max_agent_tokens_month=body.max_agent_tokens_month,
        max_tokens_per_run=body.max_tokens_per_run,
    )
    return await AdminCompanyService(session).set_agent_policy(company_id, policy)



@router.get("/{company_id}/metrics")
async def company_metrics(company_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await AdminCompanyService(session).get_metrics(company_id)


company_router = APIRouter(prefix="/companies", tags=["companies"])


@company_router.get("/{company_id}/cabinets")
async def list_company_cabinets(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    if not principal.is_platform_admin:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await EntitlementService(session).require_membership(employee.id, company_id)
    items = await AdminCompanyService(session).list_org_cabinets(company_id)
    return {"items": items}


@company_router.get("/{company_id}/employees")
async def list_company_employees(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    if not principal.is_platform_admin:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await EntitlementService(session).require_company_admin(employee.id, company_id)
    items = await AdminCompanyService(session).list_company_employees(company_id)
    return {"items": items}


@company_router.get("/{company_id}/summary")
async def company_summary(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    if not principal.is_platform_admin:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await EntitlementService(session).require_company_admin(employee.id, company_id)
    return await AdminCompanyService(session).get_company_summary(company_id)
