"""Cabinet Runtime HTTP — registry + free-form meta documents."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.cabinets.meta_document_service import CabinetMetaDocumentService
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/cabinets", tags=["cabinets"])


class CreateCabinetBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    company_id: str = Field(min_length=3, max_length=40)
    base_template: str = "base"


class RenameCabinetBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)


class PutMetaDocumentBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: Any


@router.post("")
async def create_cabinet(
    body: CreateCabinetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None:
        from prodavan.domain.errors import AppError

        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="employee required to create cabinet",
        )
    return await CabinetInstanceService(session).create_from_base(
        name=body.name,
        company_id=body.company_id,
        employee=employee,
        base_template=body.base_template,
    )


@router.get("")
async def list_cabinets(
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    items = await CabinetInstanceService(session).list_for_actor(
        principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/{cabinet_id}")
async def get_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).get(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.patch("/{cabinet_id}")
async def rename_cabinet(
    cabinet_id: str,
    body: RenameCabinetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).rename(
        cabinet_id=cabinet_id, name=body.name, principal=principal, employee=employee
    )


@router.post("/{cabinet_id}/archive")
async def archive_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).archive(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.delete("/{cabinet_id}")
async def delete_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).hard_delete(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/meta/documents")
async def list_meta_documents(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    items = await CabinetMetaDocumentService(session).list_documents(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/{cabinet_id}/meta/documents/{slug}")
async def get_meta_document(
    cabinet_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetMetaDocumentService(session).get_document(
        cabinet_id=cabinet_id, slug=slug, principal=principal, employee=employee
    )


@router.put("/{cabinet_id}/meta/documents/{slug}")
async def put_meta_document(
    cabinet_id: str,
    slug: str,
    body: PutMetaDocumentBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetMetaDocumentService(session).put_document(
        cabinet_id=cabinet_id,
        slug=slug,
        body=body.body,
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/meta/documents/{slug}")
async def delete_meta_document(
    cabinet_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> None:
    await CabinetMetaDocumentService(session).delete_document(
        cabinet_id=cabinet_id, slug=slug, principal=principal, employee=employee
    )
