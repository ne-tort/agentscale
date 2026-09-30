"""Cabinet Runtime HTTP — registry + bound modules and per-cabinet data."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, UploadFile
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.agent.chat_sidebar_service import ChatSidebarService
from prodavan.application.cabinets.cabinet_module_secret_service import CabinetModuleSecretService
from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.content.cabinet_upload_service import CabinetContentUploadService
from prodavan.application.modules.chat_scope import SESSION_HEADER
from prodavan.application.modules.module_action_executor import ModuleActionExecutor
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


class ActionInvokeBody(BaseModel):
    model_config = {"extra": "forbid"}

    row_id: str | None = None
    # When set, row SoT is the project leaf instance (hubs), not cabinet.
    project_id: str | None = Field(default=None, min_length=3, max_length=40)


class SecretUploadBody(BaseModel):
    model_config = {"extra": "forbid"}

    secret: str = Field(min_length=1, max_length=8192)
    label: str | None = Field(default=None, max_length=200)


class ProjectSelectionBody(BaseModel):
    model_config = {"extra": "forbid"}

    project_id: str | None = Field(default=None, max_length=40)


@router.get("/{cabinet_id}/me/selection")
async def get_project_selection(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None:
        from prodavan.domain.errors import AppError

        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await ChatSidebarService(session).get_selection(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


@router.put("/{cabinet_id}/me/selection")
async def put_project_selection(
    cabinet_id: str,
    body: ProjectSelectionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None:
        from prodavan.domain.errors import AppError

        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await ChatSidebarService(session).set_selection(
        cabinet_id=cabinet_id,
        project_id=body.project_id,
        principal=principal,
        employee=employee,
    )


@router.get("/{cabinet_id}/chats/sidebar")
async def get_chats_sidebar(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    if employee is None:
        from prodavan.domain.errors import AppError

        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await ChatSidebarService(session).sidebar(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )


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


@router.get("/{cabinet_id}/metrics")
async def get_cabinet_metrics(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    await CabinetInstanceService(session).get(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).cabinet_metrics(cabinet_id)


@router.get("/{cabinet_id}/metrics/series")
async def get_cabinet_metrics_series(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    metric: str = "agent_tokens",
    window: str = "7d",
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    await CabinetInstanceService(session).get(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).counter_series(
        metric=metric,
        entity_type="cabinet",
        entity_id=cabinet_id,
        window=window,
    )


@router.post("/{cabinet_id}/presence/heartbeat")
async def cabinet_presence_heartbeat(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    """Refresh Redis presence while employee cabinet shell is open."""
    from prodavan.application.metrics.publish import publish_presence_heartbeat
    from prodavan.domain.errors import AppError

    cab = await CabinetInstanceService(session).get(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    if employee is None:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="employee required for presence heartbeat",
        )
    company_id = str(cab.get("company_id") or "") or None
    await publish_presence_heartbeat(
        employee_id=employee.id,
        cabinet_id=cabinet_id,
        company_id=company_id,
    )
    return {"ok": True, "employee_id": employee.id, "cabinet_id": cabinet_id}


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


@router.get("/{cabinet_id}/modules/{module_id}/bound-projects")
async def list_cabinet_module_bound_projects(
    cabinet_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    """Alive projects in this cabinet with an explicit MP bind to the module.

    Soft-deleted projects are excluded even if an MP row still exists.
    """
    from sqlalchemy import select

    from prodavan.application.cabinets.access import CabinetAccessService
    from prodavan.application.modules.module_binding_service import ModuleBindingService
    from prodavan.domain.lifecycle import project_alive_clause
    from prodavan.infrastructure.persistence.models.projects import ProjectRow

    await CabinetAccessService(session).require_access(
        cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
    )
    bindings = ModuleBindingService(session)
    if not await bindings.has_cabinet_binding(module_id, cabinet_id):
        return {"items": []}
    bound_ids = list(await bindings.list_project_ids(module_id))
    if not bound_ids:
        return {"items": []}
    q = await session.execute(
        select(ProjectRow.id, ProjectRow.name)
        .where(
            ProjectRow.cabinet_id == cabinet_id,
            ProjectRow.id.in_(bound_ids),
            project_alive_clause(ProjectRow),
        )
        .order_by(ProjectRow.name)
    )
    return {"items": [{"project_id": pid, "name": name} for pid, name in q.all()]}


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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    items = await CabinetModuleService(session).list_data_rows(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    return await CabinetModuleService(session).create_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    return await CabinetModuleService(session).update_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
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
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    return await CabinetModuleService(session).delete_data_row(
        cabinet_id=cabinet_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
    )


@router.post("/{cabinet_id}/modules/{module_id}/secrets/upload")
async def upload_module_secret(
    cabinet_id: str,
    module_id: str,
    body: SecretUploadBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await CabinetModuleSecretService(session).upload_secret(
        cabinet_id=cabinet_id,
        module_id=module_id,
        secret=body.secret,
        label=body.label,
        principal=principal,
        employee=employee,
    )


@router.post("/{cabinet_id}/modules/{module_id}/actions/{action_id}/invoke")
async def invoke_module_action(
    cabinet_id: str,
    module_id: str,
    action_id: str,
    body: ActionInvokeBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    return await ModuleActionExecutor(session).invoke(
        cabinet_id=cabinet_id,
        module_id=module_id,
        action_id=action_id,
        principal=principal,
        employee=employee,
        row_id=body.row_id,
        project_id=body.project_id,
        session_id=x_prodavan_session_id,
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

