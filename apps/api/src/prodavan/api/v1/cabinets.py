"""Cabinet Runtime HTTP surface (L06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.cabinets import (
    CabinetAuditService,
    CabinetBundleService,
    CabinetInstanceService,
    CabinetMcpDispatcher,
    CabinetMetaService,
    CabinetPackagesService,
    CabinetRowsService,
    CabinetWorkspaceDocsService,
    get_materialize_port,
    list_platform_tools,
)
from prodavan.domain.cabinets import StorageKind
from prodavan.domain.errors import AppError
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


class ColumnDef(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=64)
    type: str
    required: bool = False
    unique: bool = False
    ref_table_slug: str | None = None


class CreateTableBody(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    storage_kind: str = StorageKind.PHYSICAL
    columns: list[ColumnDef] = Field(min_length=1)


class AddColumnBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=64)
    type: str
    required: bool = False
    unique: bool = False
    ref_table_slug: str | None = None


class UpdateColumnBody(BaseModel):
    model_config = {"extra": "forbid"}

    type: str | None = None
    required: bool | None = None
    unique: bool | None = None
    ref_table_slug: str | None = None


class CreateViewBody(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=1, max_length=64)
    table_slug: str | None = Field(default=None, max_length=64)
    ui_json: dict = Field(default_factory=lambda: {"version": 1, "kind": "collection", "title_field": "title"})
    version: int = Field(default=1, ge=1)


class CreateTabBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    order: int = Field(ge=0, le=9999)
    view_slug: str = Field(min_length=1, max_length=64)


class UpdateViewBody(BaseModel):
    model_config = {"extra": "forbid"}

    table_slug: str | None = Field(default=None, max_length=64)
    ui_json: dict | None = None
    version: int | None = Field(default=None, ge=1)


class UpdateTabBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, min_length=1, max_length=200)
    order: int | None = Field(default=None, ge=0, le=9999)
    view_slug: str | None = Field(default=None, min_length=1, max_length=64)


class UpdateTableBody(BaseModel):
    model_config = {"extra": "forbid"}

    label: str = Field(min_length=1, max_length=200)


class UpsertRowBody(BaseModel):
    model_config = {"extra": "forbid"}

    values: dict = Field(default_factory=dict)
    id: str | None = Field(default=None, max_length=40)


class WorkspaceDocBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: str = Field(default="", max_length=200_000)


class McpCallBody(BaseModel):
    model_config = {"extra": "forbid"}

    tool: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)


class ImportBundleBody(BaseModel):
    model_config = {"extra": "forbid"}

    company_id: str = Field(min_length=3, max_length=40)
    zip_base64: str = Field(min_length=8)
    name: str | None = Field(default=None, max_length=200)


class DeployPackageBody(BaseModel):
    model_config = {"extra": "forbid"}

    zip_base64: str = Field(min_length=8)
    replace_if_name: bool = False


EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


@router.post("", status_code=201)
async def create_cabinet(
    body: CreateCabinetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None and not principal.is_platform_admin:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    if employee is None:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="platform admin cannot create cabinet without employee context",
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
    employee: EmployeeDep,
) -> list[dict]:
    return await CabinetInstanceService(session).list_for_actor(principal=principal, employee=employee)


@router.post("/import", status_code=201)
async def import_cabinet_bundle(
    body: ImportBundleBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await CabinetBundleService(session).import_base64(
        zip_base64=body.zip_base64,
        company_id=body.company_id,
        employee=employee,
        name_override=body.name,
    )


@router.get("/{cabinet_id}")
async def get_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
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
    employee: EmployeeDep,
) -> dict:
    return await CabinetInstanceService(session).rename(
        cabinet_id=cabinet_id,
        name=body.name,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/archive")
async def archive_cabinet(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetInstanceService(session).archive(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/bundle")
async def export_cabinet_bundle(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    include_data: bool = True,
) -> dict:
    return await CabinetBundleService(session).export_base64(
        cabinet_id=cabinet_id,
        principal=principal,
        employee=employee,
        include_data=include_data,
    )


@router.get("/{cabinet_id}/mcp-packages")
async def list_mcp_packages(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> list[dict]:
    return await CabinetPackagesService(session).list_packages(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.post("/{cabinet_id}/mcp-packages", status_code=201)
async def deploy_mcp_package(
    cabinet_id: str,
    body: DeployPackageBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetPackagesService(session).deploy_base64(
        cabinet_id=cabinet_id,
        zip_base64=body.zip_base64,
        principal=principal,
        employee=employee,
        replace_if_name=body.replace_if_name,
    )


@router.post("/{cabinet_id}/mcp-packages/{name}/disable")
async def disable_mcp_package(
    cabinet_id: str,
    name: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetPackagesService(session).disable(
        cabinet_id=cabinet_id, name=name, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/meta/tables")
async def list_meta_tables(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    include_archived: bool = False,
) -> list[dict]:
    return await CabinetMetaService(session).list_tables(
        cabinet_id=cabinet_id,
        principal=principal,
        employee=employee,
        include_archived=include_archived,
    )


@router.get("/{cabinet_id}/meta/tables/{table_slug}")
async def get_meta_table(
    cabinet_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).get_table(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/meta/tables", status_code=201)
async def create_meta_table(
    cabinet_id: str,
    body: CreateTableBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).create_table(
        cabinet_id=cabinet_id,
        slug=body.slug,
        label=body.label,
        storage_kind=body.storage_kind,
        columns=[c.model_dump() for c in body.columns],
        principal=principal,
        employee=employee,
    )


@router.patch("/{cabinet_id}/meta/tables/{table_slug}")
async def update_meta_table(
    cabinet_id: str,
    table_slug: str,
    body: UpdateTableBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).update_table(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        patch=body.model_dump(exclude_unset=True),
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/meta/tables/{table_slug}/archive")
async def archive_meta_table(
    cabinet_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).archive_table(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/meta/tables/{table_slug}", status_code=204)
async def delete_meta_table(
    cabinet_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> None:
    await CabinetMetaService(session).delete_table(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/meta/tables/{table_slug}/columns", status_code=201)
async def add_meta_column(
    cabinet_id: str,
    table_slug: str,
    body: AddColumnBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).add_column(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        column=body.model_dump(),
        principal=principal,
        employee=employee,
    )


@router.patch("/{cabinet_id}/meta/tables/{table_slug}/columns/{column_name}")
async def update_meta_column(
    cabinet_id: str,
    table_slug: str,
    column_name: str,
    body: UpdateColumnBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="empty patch",
        )
    return await CabinetMetaService(session).update_column(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        column_name=column_name,
        patch=patch,
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/meta/tables/{table_slug}/columns/{column_name}", status_code=204)
async def delete_meta_column(
    cabinet_id: str,
    table_slug: str,
    column_name: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> None:
    await CabinetMetaService(session).delete_column(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        column_name=column_name,
        principal=principal,
        employee=employee,
    )


@router.get("/{cabinet_id}/meta/views")
async def list_meta_views(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> list[dict]:
    return await CabinetMetaService(session).list_views(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.post("/{cabinet_id}/meta/views", status_code=201)
async def create_meta_view(
    cabinet_id: str,
    body: CreateViewBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).create_view(
        cabinet_id=cabinet_id,
        slug=body.slug,
        table_slug=body.table_slug,
        ui_json=body.ui_json,
        version=body.version,
        principal=principal,
        employee=employee,
    )


@router.patch("/{cabinet_id}/meta/views/{view_slug}")
async def update_meta_view(
    cabinet_id: str,
    view_slug: str,
    body: UpdateViewBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="empty patch")
    return await CabinetMetaService(session).update_view(
        cabinet_id=cabinet_id,
        view_slug=view_slug,
        patch=patch,
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/meta/views/{view_slug}", status_code=204)
async def delete_meta_view(
    cabinet_id: str,
    view_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> None:
    await CabinetMetaService(session).delete_view(
        cabinet_id=cabinet_id,
        view_slug=view_slug,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/meta/tabs", status_code=201)
async def create_meta_tab(
    cabinet_id: str,
    body: CreateTabBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetMetaService(session).create_tab(
        cabinet_id=cabinet_id,
        title=body.title,
        order=body.order,
        view_slug=body.view_slug,
        principal=principal,
        employee=employee,
    )


@router.patch("/{cabinet_id}/meta/tabs/{tab_id}")
async def update_meta_tab(
    cabinet_id: str,
    tab_id: str,
    body: UpdateTabBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="empty patch")
    return await CabinetMetaService(session).update_tab(
        cabinet_id=cabinet_id,
        tab_id=tab_id,
        title=patch.get("title"),
        order=patch.get("order"),
        view_slug=patch.get("view_slug"),
        principal=principal,
        employee=employee,
    )


@router.delete("/{cabinet_id}/meta/tabs/{tab_id}", status_code=204)
async def delete_meta_tab(
    cabinet_id: str,
    tab_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> None:
    await CabinetMetaService(session).delete_tab(
        cabinet_id=cabinet_id,
        tab_id=tab_id,
        principal=principal,
        employee=employee,
    )


@router.get("/{cabinet_id}/meta/tabs")
async def list_meta_tabs(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> list[dict]:
    return await CabinetMetaService(session).list_tabs(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/data/{table_slug}/rows")
async def query_rows(
    cabinet_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return await CabinetRowsService(session).query_rows(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
        limit=limit,
        offset=offset,
    )


@router.post("/{cabinet_id}/data/{table_slug}/rows")
async def upsert_row(
    cabinet_id: str,
    table_slug: str,
    body: UpsertRowBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> JSONResponse:
    result = await CabinetRowsService(session).upsert_row(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        values=body.values,
        row_id=body.id,
        principal=principal,
        employee=employee,
    )
    return JSONResponse(content=result, status_code=200 if body.id else 201)


@router.delete("/{cabinet_id}/data/{table_slug}/rows/{row_id}", status_code=204)
async def delete_row(
    cabinet_id: str,
    table_slug: str,
    row_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> None:
    await CabinetRowsService(session).delete_row(
        cabinet_id=cabinet_id,
        table_slug=table_slug,
        row_id=row_id,
        principal=principal,
        employee=employee,
    )


@router.get("/{cabinet_id}/mcp/tools")
async def mcp_list_tools(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """List platform cabinet.* tools (scoped: requires cabinet access)."""
    from prodavan.application.cabinets.access import CabinetAccessService

    await CabinetAccessService(session).require_access(
        cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
    )
    return {"tools": list_platform_tools()}


@router.post("/{cabinet_id}/mcp/call")
async def mcp_call(
    cabinet_id: str,
    body: McpCallBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Invoke a platform cabinet.* tool — same surface agents get after materialize."""
    result = await CabinetMcpDispatcher(session).call(
        cabinet_id=cabinet_id,
        tool=body.tool,
        arguments=body.arguments,
        principal=principal,
        employee=employee,
    )
    return {"tool": body.tool, "result": result}


@router.get("/{cabinet_id}/audit-events")
async def list_audit_events(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    limit: int = 50,
) -> list[dict]:
    return await CabinetAuditService(session).list_events(
        cabinet_id=cabinet_id,
        principal=principal,
        employee=employee,
        limit=limit,
    )


@router.get("/{cabinet_id}/workspace-docs")
async def list_workspace_docs(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> list[dict]:
    return await CabinetWorkspaceDocsService(session).list_docs(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.get("/{cabinet_id}/workspace-docs/{slug}")
async def get_workspace_doc(
    cabinet_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetWorkspaceDocsService(session).get_doc(
        cabinet_id=cabinet_id, slug=slug, principal=principal, employee=employee
    )


@router.put("/{cabinet_id}/workspace-docs/{slug}")
async def put_workspace_doc(
    cabinet_id: str,
    slug: str,
    body: WorkspaceDocBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await CabinetWorkspaceDocsService(session).put_doc(
        cabinet_id=cabinet_id,
        slug=slug,
        body=body.body,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/materialize-stub")
async def materialize_stub(
    cabinet_id: str,
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Stable hook interface preview — FS implementation in L07."""
    from prodavan.application.cabinets.access import CabinetAccessService

    await CabinetAccessService(session).require_access(
        cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
    )
    result = await get_materialize_port().materialize_project(
        session=session,
        project_id=project_id,
        cabinet_id=cabinet_id,
    )
    return {
        "project_id": result.project_id,
        "cabinet_id": result.cabinet_id,
        "workspace_root": result.workspace_root,
        "mcp_config_path": result.mcp_config_path,
        "status": result.status,
    }
