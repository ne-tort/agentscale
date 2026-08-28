"""Company-scoped modules — local CRUD + platform grants + cabinet bind."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.identity.service import EntitlementService
from prodavan.application.modules.company_module_service import CompanyModuleService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}/modules", tags=["company-modules"])


class CreateCompanyModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)


class PatchCompanyModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    cabinet_ids: list[str] | None = None


class MetaDocumentBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: Any


class CopyModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.get("")
async def list_company_modules(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    items = await CompanyModuleService(session).list_for_company(company_id)
    return {"items": items}


@router.post("", status_code=201)
async def create_company_module(
    company_id: str,
    body: CreateCompanyModuleBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).create_local(company_id=company_id, name=body.name)


@router.get("/{module_id}")
async def get_company_module(
    company_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).get_for_company(company_id=company_id, module_id=module_id)


@router.patch("/{module_id}")
async def patch_company_module(
    company_id: str,
    module_id: str,
    body: PatchCompanyModuleBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).update_for_company(
        company_id=company_id,
        module_id=module_id,
        name=body.name,
        cabinet_ids=body.cabinet_ids,
    )


@router.delete("/{module_id}")
async def delete_company_module(
    company_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).delete_local(company_id=company_id, module_id=module_id)


@router.post("/{module_id}/copy", status_code=201)
async def copy_company_module(
    company_id: str,
    module_id: str,
    body: CopyModuleBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).copy_module(
        company_id=company_id,
        source_module_id=module_id,
        name=body.name,
    )


@router.get("/{module_id}/meta/documents")
async def list_company_module_meta(
    company_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    items = await CompanyModuleService(session).list_meta_documents(company_id=company_id, module_id=module_id)
    return {"items": items}


@router.get("/{module_id}/meta/documents/{slug}")
async def get_company_module_meta(
    company_id: str,
    module_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).get_meta_document(
        company_id=company_id, module_id=module_id, slug=slug
    )


@router.put("/{module_id}/meta/documents/{slug}")
async def put_company_module_meta(
    company_id: str,
    module_id: str,
    slug: str,
    body: MetaDocumentBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)],
) -> dict:
    await EntitlementService(session).require_company_actor(principal, company_id, employee=employee)
    return await CompanyModuleService(session).put_meta_document(
        company_id=company_id, module_id=module_id, slug=slug, body=body.body
    )
