"""Company-scoped Project Containers API (employees' projects only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.admin.container_read_service import AdminContainerReadService
from prodavan.application.identity.service import EntitlementService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}/containers", tags=["company-containers"])


@router.get("")
async def list_company_containers(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).list_containers_for_company(company_id, limit=limit)


@router.get("/{project_id}")
async def get_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).get_container_for_company(company_id, project_id)


@router.post("/{project_id}/pause")
async def pause_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).pause_for_company(
        company_id=company_id, project_id=project_id, principal=principal
    )


@router.post("/{project_id}/resume")
async def resume_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).resume_for_company(
        company_id=company_id, project_id=project_id, principal=principal
    )


@router.delete("/{project_id}")
async def delete_company_container(
    company_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await AdminContainerReadService(session).delete_for_company(
        company_id=company_id, project_id=project_id, principal=principal
    )
