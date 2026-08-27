"""Platform Admin company control plane HTTP (L04)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, PrincipalDep, SessionDep, get_current_employee
from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.cabinets.instance_service import CabinetInstanceService
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


class CompanyPatchBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    contact_email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)


class SubscriptionBody(BaseModel):
    model_config = {"extra": "forbid"}

    subscription_lifetime: bool = False
    subscription_ends_at: str | None = None


class AgentPolicyBody(BaseModel):
    model_config = {"extra": "forbid"}

    tool_preset: str = "workspace_dev"
    preferred_provider: str | None = None
    platform_fallback: bool = True
    model_allowlist: list[str] = Field(default_factory=list)
    max_agent_tokens_month: int | None = Field(default=None, ge=1)
    max_tokens_per_run: int | None = Field(default=None, ge=1)
    max_cost_usd_month: Decimal | None = Field(default=None, gt=0)
    max_attachment_mb: int = Field(default=20, ge=1, le=500)
    idle_pause_after_hours: int | None = Field(default=None, ge=0, le=8760)
    webhook_hmac_secret: str | None = Field(default=None, max_length=256)
    telegram_hmac_secret: str | None = Field(default=None, max_length=256)


class CompanyPasswordBody(BaseModel):
    model_config = {"extra": "forbid"}

    password: str = Field(min_length=8, max_length=200)


class CompanyLoginBody(BaseModel):
    model_config = {"extra": "forbid"}

    login: str = Field(min_length=3, max_length=64)


class AssignCabinetEmployeeBody(BaseModel):
    model_config = {"extra": "forbid"}

    employee_id: str = Field(min_length=3, max_length=40)


class EmployeePasswordBody(BaseModel):
    model_config = {"extra": "forbid"}

    password: str = Field(min_length=8, max_length=200)


class EmployeeContactEmailBody(BaseModel):
    model_config = {"extra": "forbid"}

    contact_email: str | None = Field(default=None, max_length=320)


@router.get("")
async def list_companies(_: PlatformAdminDep, session: SessionDep) -> dict:
    items = await AdminCompanyService(session).list_companies()
    return {"items": items}


@router.get("/{company_id}")
async def get_company(company_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await AdminCompanyService(session).get_company(company_id)


@router.delete("/{company_id}")
async def delete_company(
    company_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminCompanyService(session).delete_company(company_id, principal=admin)


@router.post("/{company_id}/restore")
async def restore_company(
    company_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    from prodavan.application.companies.service import CompaniesCommandService

    return await CompaniesCommandService(session).restore(company_id, principal=admin)


@router.delete("/{company_id}/purge")
async def purge_company(
    company_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    from prodavan.application.companies.service import CompaniesCommandService

    return await CompaniesCommandService(session).purge(company_id, principal=admin)


@router.patch("/{company_id}")
async def patch_company(
    company_id: str,
    body: CompanyPatchBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminCompanyService(session).patch_company(
        company_id,
        **body.model_dump(exclude_unset=True),
    )


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
    return await AdminCompanyService(session).get_agent_policy_public(company_id)


@router.put("/{company_id}/agent-policy")
async def set_agent_policy(
    company_id: str,
    body: AgentPolicyBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    policy = CompanyAgentRuntimePolicy(
        tool_preset=body.tool_preset,
        preferred_provider=body.preferred_provider,
        platform_fallback=body.platform_fallback,
        model_allowlist=body.model_allowlist,
        max_agent_tokens_month=body.max_agent_tokens_month,
        max_tokens_per_run=body.max_tokens_per_run,
        max_cost_usd_month=body.max_cost_usd_month,
        max_attachment_mb=body.max_attachment_mb,
        idle_pause_after_hours=body.idle_pause_after_hours,
        webhook_hmac_secret=fields.get("webhook_hmac_secret") if "webhook_hmac_secret" in fields else None,
        telegram_hmac_secret=fields.get("telegram_hmac_secret") if "telegram_hmac_secret" in fields else None,
    )
    return await AdminCompanyService(session).set_agent_policy(
        company_id,
        policy,
        update_webhook_secret="webhook_hmac_secret" in fields,
        update_telegram_secret="telegram_hmac_secret" in fields,
    )


@router.post("/{company_id}/idle-pause/sweep")
async def sweep_idle_pause_company(
    company_id: str,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    from prodavan.application.projects.idle_pause_service import IdlePauseService

    return await IdlePauseService(session).sweep_company(company_id, principal=admin)


@router.put("/{company_id}/subscription")
async def set_company_subscription(
    company_id: str,
    body: SubscriptionBody,
    principal: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    ends_at: datetime | None = None
    if body.subscription_ends_at:
        try:
            raw = body.subscription_ends_at.replace("Z", "+00:00")
            ends_at = datetime.fromisoformat(raw)
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)
        except ValueError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="invalid subscription_ends_at",
            ) from exc
    if not body.subscription_lifetime and ends_at is None:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="subscription_ends_at required unless subscription_lifetime",
        )
    return await AdminCompanyService(session).set_subscription(
        company_id,
        subscription_ends_at=ends_at,
        subscription_lifetime=body.subscription_lifetime,
        principal=principal,
    )


@router.get("/{company_id}/metrics")
async def company_metrics(company_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await AdminCompanyService(session).get_metrics(company_id)


@router.put("/{company_id}/password")
async def set_company_password_admin(
    company_id: str,
    body: CompanyPasswordBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminCompanyService(session).set_company_password(
        company_id, password=body.password
    )


@router.put("/{company_id}/login")
async def set_company_login_admin(
    company_id: str,
    body: CompanyLoginBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await AdminCompanyService(session).set_company_login(company_id, login=body.login)


company_router = APIRouter(prefix="/companies", tags=["companies"])


@company_router.get("/{company_id}/cabinets")
async def list_company_cabinets(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    if not principal.is_platform_admin:
        if principal.is_company_principal:
            await EntitlementService(session).require_company_actor(
                principal, company_id, employee=employee
            )
        else:
            if employee is None:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
            await EntitlementService(session).require_membership(employee.id, company_id)
    items = await AdminCompanyService(session).list_org_cabinets(company_id)
    return {"items": items}


@company_router.get("/{company_id}/cabinets/{cabinet_id}/assignments")
async def list_cabinet_assignments(
    company_id: str,
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    items = await CabinetInstanceService(session).list_assignments_for_company(
        cabinet_id=cabinet_id,
        company_id=company_id,
        principal=principal,
        employee=employee,
    )
    return {"items": items}


@company_router.post("/{company_id}/cabinets/{cabinet_id}/assignments")
async def assign_cabinet_employee(
    company_id: str,
    cabinet_id: str,
    body: AssignCabinetEmployeeBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CabinetInstanceService(session).assign_employee(
        cabinet_id=cabinet_id,
        company_id=company_id,
        employee_id=body.employee_id,
        principal=principal,
        employee=employee,
    )


@company_router.delete("/{company_id}/cabinets/{cabinet_id}/assignments/{employee_id}")
async def revoke_cabinet_assignment(
    company_id: str,
    cabinet_id: str,
    employee_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CabinetInstanceService(session).revoke_employee_assignment(
        cabinet_id=cabinet_id,
        company_id=company_id,
        employee_id=employee_id,
        principal=principal,
        employee=employee,
    )


@company_router.get("/{company_id}/employees")
async def list_company_employees(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    items = await AdminCompanyService(session).list_company_employees(company_id)
    return {"items": items}


@company_router.put("/{company_id}/employees/{employee_id}/password")
async def set_employee_password(
    company_id: str,
    employee_id: str,
    body: EmployeePasswordBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminCompanyService(session).set_employee_password(
        company_id, employee_id, password=body.password
    )


@company_router.patch("/{company_id}/employees/{employee_id}/contact-email")
async def update_employee_contact_email(
    company_id: str,
    employee_id: str,
    body: EmployeeContactEmailBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminCompanyService(session).update_employee_contact_email(
        company_id, employee_id, contact_email=body.contact_email
    )


@company_router.get("/{company_id}/summary")
async def company_summary(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminCompanyService(session).get_company_summary(company_id)


@company_router.put("/{company_id}/password")
async def set_company_password(
    company_id: str,
    body: CompanyPasswordBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminCompanyService(session).set_company_password(
        company_id, password=body.password
    )
