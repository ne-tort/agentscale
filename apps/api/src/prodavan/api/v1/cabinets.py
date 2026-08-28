"""Cabinet Runtime HTTP — registry + bound modules and per-cabinet data."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.content.cabinet_upload_service import CabinetContentUploadService
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


class CopyCabinetBody(BaseModel):
    model_config = {"extra": "forbid"}

    company_id: str = Field(min_length=3, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class DataRowBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: dict[str, Any]


@router.post("")
async def create_cabinet(
    body: CreateCabinetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None and not principal.is_company_principal:
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
        principal=principal,
        employee=employee,
        base_template=body.base_template,
    )


@router.post("/{cabinet_id}/copy", status_code=201)
async def copy_cabinet(
    cabinet_id: str,
    body: CopyCabinetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None and not principal.is_company_principal:
        from prodavan.domain.errors import AppError

        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="employee required to copy cabinet",
        )
    return await CabinetInstanceService(session).copy_cabinet(
        cabinet_id=cabinet_id,
        company_id=body.company_id,
        name=body.name,
        principal=principal,
        employee=employee,
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
    return await CabinetInstanceService(session).soft_delete(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.post("/{cabinet_id}/restore")
async def restore_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).restore(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.delete("/{cabinet_id}/purge")
async def purge_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetInstanceService(session).hard_delete(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/modules")
async def list_cabinet_modules(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    items = await CabinetModuleService(session).list_modules(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/{cabinet_id}/modules/{module_id}/meta/documents/{slug}")
async def get_cabinet_module_meta(
    cabinet_id: str,
    module_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetModuleService(session).get_meta_document(
        cabinet_id=cabinet_id,
        module_id=module_id,
        slug=slug,
        principal=principal,
        employee=employee,
    )


@router.get("/{cabinet_id}/modules/{module_id}/data/{table_slug}")
async def list_module_data_rows(
    cabinet_id: str,
    module_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    items = await CabinetModuleService(session).list_data_rows(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
    )
    return {"items": items}


@router.post("/{cabinet_id}/modules/{module_id}/data/{table_slug}")
async def create_module_data_row(
    cabinet_id: str,
    module_id: str,
    table_slug: str,
    body: DataRowBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetModuleService(session).create_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
        principal=principal,
        employee=employee,
    )


@router.patch("/{cabinet_id}/modules/{module_id}/data/{table_slug}/{row_id}")
async def update_module_data_row(
    cabinet_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    body: DataRowBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetModuleService(session).update_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/modules/{module_id}/data/{table_slug}/{row_id}")
async def delete_module_data_row(
    cabinet_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> None:
    await CabinetModuleService(session).delete_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/content/upload")
async def upload_cabinet_content(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    file: UploadFile = File(...),
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    data = await file.read()
    return await CabinetContentUploadService(session).upload_for_cabinet(
        cabinet_id=cabinet_id,
        data=data,
        filename=file.filename or "upload.bin",
        mime=file.content_type,
        principal=principal,
        employee=employee,
    )
