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
from prodavan.infrastructure.keycloak.invite import get_invite_client
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
    admin_email: str = Field(min_length=3, max_length=320)
    admin_display_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_password(cls, data: object) -> object:
        if isinstance(data, dict) and "password" in data:
            raise ValueError("password is not accepted; invite via Keycloak")
        return data


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
    contours = svc.contours_for(principal, employee)
    return {
        "sub": principal.sub,
        "email": principal.email,
        "roles": sorted(principal.roles),
        "contours": contours,
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
    company, admin = await IdentityCommandService(session, get_invite_client()).create_company_with_admin(
        name=body.name,
        admin_email=body.admin_email,
        admin_display_name=body.admin_display_name,
    )
    return {
        "company": {"id": company.id, "name": company.name},
        "admin_employee": {"id": admin.id, "email": admin.email, "status": admin.status},
    }


@router.post("/companies/{company_id}/employees", status_code=201)
async def invite_employee(
    company_id: str,
    body: InviteEmployeeBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    svc = EntitlementService(session)
    if not principal.is_platform_admin:
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        await svc.require_company_admin(employee.id, company_id)
    emp = await IdentityCommandService(session, get_invite_client()).invite_employee(
        company_id=company_id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    return {"id": emp.id, "email": emp.email, "status": emp.status}


@router.post("/employees/{employee_id}/disable")
async def disable_employee(
    employee_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    actor: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    if not principal.is_platform_admin:
        if actor is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
        q = await session.execute(
            select(EmployeeRow)
            .where(EmployeeRow.id == employee_id)
            .options(selectinload(EmployeeRow.memberships))
        )
        target = q.scalar_one_or_none()
        if target is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        actor_companies = {
            m.company_id for m in actor.memberships if m.role == MembershipRole.COMPANY_ADMIN
        }
        target_companies = {m.company_id for m in target.memberships}
        if not actor_companies.intersection(target_companies):
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="forbidden")
    emp = await IdentityCommandService(session, get_invite_client()).disable_employee(
        employee_id=employee_id,
        principal=principal,
    )
    return {"id": emp.id, "status": emp.status}


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
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    await EntitlementService(session).require_membership(employee.id, company_id)
    return {"company_id": company_id, "jwt_reissued": False}
