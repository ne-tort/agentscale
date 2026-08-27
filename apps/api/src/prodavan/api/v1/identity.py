"""Identity HTTP surface (L01) — no password login."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import (
    PlatformAdminDep,
    PrincipalDep,
    SessionDep,
    WorkContextDep,
    get_current_employee,
)
from prodavan.application.identity.service import EntitlementService, IdentityCommandService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import MembershipRole
from prodavan.infrastructure.keycloak.provisioning import get_provisioning
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

router = APIRouter(tags=["identity"])


async def _memberships_public(session: AsyncSession, memberships: list) -> list[dict]:
    out: list[dict] = []
    for m in memberships:
        company = await session.get(CompanyRow, m.company_id)
        out.append(
            {
                "company_id": m.company_id,
                "company_name": company.name if company is not None else m.company_id,
                "role": m.role,
            }
        )
    return out


class CreateCompanyBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    password: str = Field(
        min_length=8,
        max_length=200,
        description="Company Keycloak password; login username = company id",
    )
    description: str | None = Field(default=None, max_length=2000)
    contact_email: str | None = Field(
        default=None,
        max_length=320,
        description="Optional contact email — not used for company login",
    )
    admin_email: str | None = Field(
        default=None,
        max_length=320,
        description="Optional human Employee soft-linked as company.admin",
    )
    admin_display_name: str | None = None


class InviteEmployeeBody(BaseModel):
    model_config = {"extra": "forbid"}

    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = None
    role: str = MembershipRole.MEMBER

    @model_validator(mode="before")
    @classmethod
    def _reject_password(cls, data: object) -> object:
        if isinstance(data, dict) and "password" in data:
            raise ValueError("password is not accepted; invite via Keycloak")
        return data


@router.get("/me")
async def me(
    principal: PrincipalDep,
    ctx: WorkContextDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    session: SessionDep,
) -> dict:
    svc = EntitlementService(session)
    company = None
    if principal.is_company_principal:
        company = await svc.ensure_company_principal(principal)
    contours = svc.contours_for(principal, employee, company=company)
    return {
        "sub": principal.sub,
        "email": principal.email,
        "roles": sorted(principal.roles),
        "contours": contours,
        "company": None
        if company is None
        else {
            "id": company.id,
            "name": company.name,
            "username": company.id,
            "contact_email": company.contact_email,
        },
        "employee": None
        if employee is None
        else {
            "id": employee.id,
            "status": employee.status,
            "email": employee.email,
            "memberships": await _memberships_public(session, list(employee.memberships)),
        },
        "work_context": {
            "cabinet_id": ctx.cabinet_id,
            "project_id": ctx.project_id,
        },
    }


@router.post("/companies", status_code=201)
async def create_company(
    body: CreateCompanyBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    company, admin = await IdentityCommandService(session, get_provisioning()).create_company_with_admin(
        name=body.name,
        password=body.password,
        description=body.description,
        contact_email=body.contact_email,
        admin_email=body.admin_email,
        admin_display_name=body.admin_display_name,
    )
    out: dict = {
        "company": {
            "id": company.id,
            "name": company.name,
            "description": company.description,
            "username": company.id,
            "keycloak_sub": company.keycloak_sub,
            "contact_email": company.contact_email,
        },
        "credentials": {
            "username": company.id,
            "password_set": True,
        },
    }
    if admin is not None:
        out["admin_employee"] = {
            "id": admin.id,
            "email": admin.email,
            "status": admin.status,
            "keycloak_sub": admin.keycloak_sub,
        }
    return out


@router.post("/companies/{company_id}/employees", status_code=201)
async def invite_employee(
    company_id: str,
    body: InviteEmployeeBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    svc = EntitlementService(session)
    await svc.require_company_actor(principal, company_id, employee=employee)
    emp = await IdentityCommandService(session, get_provisioning()).invite_employee(
        company_id=company_id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    return {"id": emp.id, "email": emp.email, "status": emp.status, "keycloak_sub": emp.keycloak_sub}


@router.post("/employees/{employee_id}/disable")
async def disable_employee(
    employee_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    actor: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    svc = EntitlementService(session)
    if not principal.is_platform_admin:
        q = await session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        target = q.scalar_one_or_none()
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        if principal.is_company_principal:
            company = await svc.ensure_company_principal(principal)
            assert company is not None
            target_companies = {m.company_id for m in target.memberships}
            if company.id not in target_companies:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
        else:
            if actor is None:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
            actor_companies = {
                m.company_id for m in actor.memberships if m.role == MembershipRole.COMPANY_ADMIN
            }
            target_companies = {m.company_id for m in target.memberships}
            if not actor_companies.intersection(target_companies):
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
    emp = await IdentityCommandService(session, get_provisioning()).disable_employee(
        employee_id=employee_id,
        principal=principal,
    )
    return {"id": emp.id, "status": emp.status, "paused": True}


@router.delete("/employees/{employee_id}")
async def soft_delete_employee(
    employee_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    actor: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from prodavan.application.employees.service import EmployeesCommandService

    svc = EntitlementService(session)
    if not principal.is_platform_admin:
        q = await session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        target = q.scalar_one_or_none()
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        if principal.is_company_principal:
            company = await svc.ensure_company_principal(principal)
            assert company is not None
            target_companies = {m.company_id for m in target.memberships}
            if company.id not in target_companies:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
        else:
            if actor is None:
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
            actor_companies = {
                m.company_id for m in actor.memberships if m.role == MembershipRole.COMPANY_ADMIN
            }
            target_companies = {m.company_id for m in target.memberships}
            if not actor_companies.intersection(target_companies):
                raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
    emp = await EmployeesCommandService(session).soft_delete(
        employee_id=employee_id, principal=principal
    )
    return {"id": emp.id, "deleted": True, "soft": True, "status": emp.status}


@router.post("/session/switch-company")
async def switch_company(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    """Does NOT reissue JWT — client keeps access token; only validates membership."""
    if principal.is_platform_admin:
        return {"company_id": company_id, "jwt_reissued": False}
    if principal.is_company_principal:
        company = await EntitlementService(session).ensure_company_principal(principal)
        assert company is not None
        if company.id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
        return {"company_id": company_id, "jwt_reissued": False}
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    await EntitlementService(session).require_membership(employee.id, company_id)
    return {"company_id": company_id, "jwt_reissued": False}
