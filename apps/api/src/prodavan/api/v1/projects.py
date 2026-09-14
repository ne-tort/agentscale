"""Projects HTTP surface (L07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.api.v1.workspace_bodies import WorkspaceCopyBody, WorkspaceMoveBody
from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.application.project_service import ProjectAccessPolicy, ProjectCommand, ProjectQuery
from prodavan.application.projects import (
    ProjectAttachmentService,
    ProjectTriggerService,
)
from prodavan.application.projects.signed_ingress import enqueue_signed_trigger
from prodavan.application.modules.chat_scope import SESSION_HEADER
from prodavan.domain.errors import AppError
from prodavan.domain.projects.types import PAUSE_EXEMPT_TRIGGER_KINDS
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["projects"])
cabinet_projects_router = APIRouter(prefix="/cabinets", tags=["projects"])


class CreateProjectBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    agent_provider: str | None = Field(default=None, max_length=32)


class PatchProjectBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    about: str | None = Field(default=None, max_length=8000)
    budget_tokens: int | None = Field(default=None, ge=0)
    agent_provider: str | None = Field(default=None, max_length=32)
    resolved_ai_key_id: str | None = Field(default=None, max_length=40)


class PatchProjectModulesBody(BaseModel):
    model_config = {"extra": "forbid"}

    module_ids: list[str] = Field(default_factory=list)


class TriggerBody(BaseModel):
    model_config = {"extra": "forbid"}

    kind: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


class VisibilityBody(BaseModel):
    model_config = {"extra": "forbid"}

    visibility_mode: str = Field(min_length=1, max_length=32)


class AssignmentBody(BaseModel):
    model_config = {"extra": "forbid"}

    employee_id: str = Field(min_length=1, max_length=40)


class AttachmentBody(BaseModel):
    model_config = {"extra": "forbid"}

    filename: str = Field(min_length=1, max_length=260)
    content_base64: str = Field(min_length=1)
    content_type: str | None = Field(default=None, max_length=128)


EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


@cabinet_projects_router.post("/{cabinet_id}/projects", status_code=201)
async def create_project(
    cabinet_id: str,
    body: CreateProjectBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await ProjectCommand(session).create(
        cabinet_id=cabinet_id,
        name=body.name,
        employee=employee,
        principal=principal,
        agent_provider=body.agent_provider,
    )


@cabinet_projects_router.get("/{cabinet_id}/projects")
async def list_projects(
    cabinet_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    items = await ProjectQuery(session).list_for_cabinet(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/projects/{project_id}/metrics")
async def get_project_summary_metrics(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service import ProjectAccessPolicy

    await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=False, allow_paused=True
    )
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).project_metrics(project_id)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectQuery(session).get(project_id=project_id, principal=principal, employee=employee)


@router.get("/projects/{project_id}/container")
async def get_project_container(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectQuery(session).get_container(
        project_id=project_id, principal=principal, employee=employee
    )


@router.get("/projects/{project_id}/container/metrics")
async def get_project_container_metrics(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    window: str = "1h",
) -> dict:
    await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=False, allow_paused=True
    )
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).get_project_metrics(project_id, window=window)


@router.post("/projects/{project_id}/reload")
async def reload_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).reload_project(
        project_id=project_id, principal=principal, employee=employee
    )


@router.get("/projects/{project_id}/container/workspace/entries")
async def list_project_workspace_entries(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    path: str = Query(default=""),
) -> dict:
    return await PodWorkspaceService(session).list_entries(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/container/workspace/content")
async def download_project_workspace_content(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    path: str = Query(min_length=1),
) -> Response:
    data, entry = await PodWorkspaceService(session).read_content(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )
    safe_name = entry.name.replace('"', "")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/projects/{project_id}/container/workspace/preview")
async def preview_project_workspace_text(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    path: str = Query(min_length=1),
) -> dict:
    return await PodWorkspaceService(session).preview_text(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}/container/workspace/entries")
async def delete_project_workspace_entry(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    path: str = Query(min_length=1),
) -> dict:
    return await PodWorkspaceService(session).delete_entry(
        project_id=project_id,
        path=path,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/container/workspace/move")
async def move_project_workspace_entry(
    project_id: str,
    body: WorkspaceMoveBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await PodWorkspaceService(session).move_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/container/workspace/copy")
async def copy_project_workspace_entry(
    project_id: str,
    body: WorkspaceCopyBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await PodWorkspaceService(session).copy_entry(
        project_id=project_id,
        src=body.src,
        dst=body.dst,
        principal=principal,
        employee=employee,
    )


@router.patch("/projects/{project_id}")
async def patch_project(
    project_id: str,
    body: PatchProjectBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="no fields to update")
    return await ProjectCommand(session).patch(
        project_id=project_id,
        principal=principal,
        employee=employee,
        name=fields.get("name"),
        about=fields.get("about"),
        update_about="about" in fields,
        budget_tokens=fields.get("budget_tokens"),
        update_budget_tokens="budget_tokens" in fields,
        agent_provider=fields.get("agent_provider"),
        update_agent_provider="agent_provider" in fields,
        resolved_ai_key_id=fields.get("resolved_ai_key_id"),
        update_resolved_ai_key_id="resolved_ai_key_id" in fields,
    )


@router.get("/projects/{project_id}/modules")
async def get_project_modules(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service.module_settings import ProjectModuleSettingsService

    return await ProjectModuleSettingsService(session).list_modules(
        project_id=project_id, principal=principal, employee=employee
    )


class ProjectModuleProfileBody(BaseModel):
    model_config = {"extra": "forbid"}

    profile_id: str = Field(min_length=1, max_length=80)


@router.get("/projects/{project_id}/modules/{module_id}")
async def get_project_module(
    project_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service.module_settings import ProjectModuleSettingsService

    return await ProjectModuleSettingsService(session).get_module(
        project_id=project_id,
        module_id=module_id,
        principal=principal,
        employee=employee,
    )


@router.patch("/projects/{project_id}/modules/{module_id}/profile")
async def patch_project_module_profile(
    project_id: str,
    module_id: str,
    body: ProjectModuleProfileBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service.module_settings import ProjectModuleSettingsService

    return await ProjectModuleSettingsService(session).set_profile(
        project_id=project_id,
        module_id=module_id,
        profile_id=body.profile_id,
        principal=principal,
        employee=employee,
    )


@router.patch("/projects/{project_id}/modules")
async def patch_project_modules(
    project_id: str,
    body: PatchProjectModulesBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service import ProjectCommand

    ids = await ProjectCommand(session).set_module_ids(
        project_id=project_id,
        module_ids=body.module_ids,
        principal=principal,
        employee=employee,
    )
    return ids


class BindProjectModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    # None → resolve via default_project_bind_kind(module_id) in the handler.
    bind_kind: str | None = Field(default=None, pattern="^(local|global)$")
    child_may_edit: bool | None = None


@router.post("/projects/{project_id}/modules/{module_id}/bind")
async def bind_project_module(
    project_id: str,
    module_id: str,
    body: BindProjectModuleBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.modules.module_binding_service import ModuleBindingService
    from prodavan.application.project_service.access import ProjectAccessPolicy
    from prodavan.application.projects.workspace_sync_policy import (
        attach_workspace_sync,
        defer_or_schedule_project_sync,
    )
    from prodavan.domain.modules import default_project_bind_kind

    project = await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=True,
        allow_paused=True,
    )
    bindings = ModuleBindingService(session)
    if not await bindings.has_cabinet_binding(module_id, project.cabinet_id):
        from prodavan.domain.errors import AppError

        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="module not bound to cabinet",
        )
    kind = body.bind_kind or default_project_bind_kind(module_id).value
    result = await bindings.bind_project(
        module_id,
        project_id,
        bind_kind=kind,
        child_may_edit=body.child_may_edit,
    )
    await session.commit()
    notification = await defer_or_schedule_project_sync(
        session, project_id=project_id, source="module_bind"
    )
    await session.commit()
    return attach_workspace_sync(result, notification)


@router.delete("/projects/{project_id}/modules/{module_id}")
async def revoke_project_module(
    project_id: str,
    module_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.modules.module_binding_service import ModuleBindingService
    from prodavan.application.project_service.access import ProjectAccessPolicy
    from prodavan.application.projects.workspace_sync_policy import (
        attach_workspace_sync,
        defer_or_schedule_project_sync,
    )

    await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=True,
        allow_paused=True,
    )
    bindings = ModuleBindingService(session)
    await bindings.revoke_project(module_id, project_id)
    await session.commit()
    notification = await defer_or_schedule_project_sync(
        session, project_id=project_id, source="module_unbind"
    )
    await session.commit()
    return attach_workspace_sync(
        {"module_id": module_id, "project_id": project_id, "status": "revoked"},
        notification,
    )


class ProjectRuntimeDataBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: dict = Field(default_factory=dict)


@router.get("/projects/{project_id}/runtime-modules")
async def list_project_runtime_modules(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    items = await ProjectRuntimeModuleService(session).list_modules(
        project_id=project_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/projects/{project_id}/runtime-modules/{module_id}/meta/documents/{slug}")
async def get_project_runtime_module_meta(
    project_id: str,
    module_id: str,
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    return await ProjectRuntimeModuleService(session).get_meta_document(
        project_id=project_id,
        module_id=module_id,
        slug=slug,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}")
async def list_project_runtime_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    items = await ProjectRuntimeModuleService(session).list_data_rows(
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
    )
    return {"items": items}


@router.post("/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}")
async def create_project_runtime_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    body: ProjectRuntimeDataBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    return await ProjectRuntimeModuleService(session).create_data_row(
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
    )


@router.patch("/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}/{row_id}")
async def update_project_runtime_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    body: ProjectRuntimeDataBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    x_prodavan_session_id: str | None = Header(default=None, alias=SESSION_HEADER),
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    return await ProjectRuntimeModuleService(session).update_data_row(
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
        principal=principal,
        employee=employee,
        session_id=x_prodavan_session_id,
    )


@router.delete("/projects/{project_id}/runtime-modules/{module_id}/data/{table_slug}/{row_id}")
async def delete_project_runtime_module_data(
    project_id: str,
    module_id: str,
    table_slug: str,
    row_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.projects.project_runtime_module_service import (
        ProjectRuntimeModuleService,
    )

    return await ProjectRuntimeModuleService(session).delete_data_row(
        project_id=project_id,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/ai-keys/available")
async def list_project_ai_keys(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.ai_keys.service import AiKeysService
    from prodavan.application.project_service import ProjectAccessPolicy

    row = await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=False, allow_paused=True
    )
    items = await AiKeysService(session).list_available_keys_for_project(project=row)
    return {"items": items}


@router.get("/projects/{project_id}/models/live")
async def list_project_models_live(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.ai_models.live_service import AiModelsLiveService
    from prodavan.application.project_service import ProjectAccessPolicy

    row = await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=False, allow_paused=True
    )
    key_id = getattr(row, "resolved_ai_key_id", None)
    if not key_id:
        return {"models": [], "default_model": None, "source": "catalog"}
    return await AiModelsLiveService(session).list_live_for_key(
        company_id=row.company_id,
        key_id=str(key_id),
        project_id=project_id,
    )


@router.post("/projects/{project_id}/launch")
async def launch_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).launch(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/materialize")
async def rematerialize_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).sync_project(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/sync")
async def sync_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).sync_project(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/dismiss-workspace-outdated")
async def dismiss_workspace_outdated(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).dismiss_workspace_outdated(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/agent/reset")
async def reset_project_agent(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.session_service import AgentSessionService
    from prodavan.application.project_service import ProjectAccessPolicy
    from prodavan.domain.projects import ProjectStatus

    row = await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=True,
        allow_paused=True,
    )
    if row.status == ProjectStatus.DRAFT:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="project not launched",
        )
    result = await AgentSessionService(session).reset_for_project(project_id=project_id)
    await session.commit()
    return result


@router.post("/projects/{project_id}/pause")
async def pause_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).pause(project_id=project_id, principal=principal, employee=employee)


@router.post("/projects/{project_id}/resume")
async def resume_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).resume(project_id=project_id, principal=principal, employee=employee)


@router.post("/projects/{project_id}/complete")
async def complete_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.project_service import ProjectCommand

    return await ProjectCommand(session).complete(
        project_id=project_id, principal=principal, employee=employee
    )


@router.put("/projects/{project_id}/visibility")
async def set_project_visibility(
    project_id: str,
    body: VisibilityBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).set_visibility(
        project_id=project_id,
        visibility_mode=body.visibility_mode,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/assignments", status_code=201)
async def assign_project_employee(
    project_id: str,
    body: AssignmentBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).assign_employee(
        project_id=project_id,
        employee_id=body.employee_id,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}/assignments/{employee_id}")
async def revoke_project_employee(
    project_id: str,
    employee_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).revoke_employee(
        project_id=project_id,
        employee_id=employee_id,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).delete(project_id=project_id, principal=principal, employee=employee)


@router.post("/projects/{project_id}/restore")
async def restore_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).restore(
        project_id=project_id, principal=principal, employee=employee
    )


@router.delete("/projects/{project_id}/purge")
async def purge_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectCommand(session).purge(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/triggers", status_code=202)
async def post_trigger(
    project_id: str,
    body: TriggerBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    allow_paused = body.kind in PAUSE_EXEMPT_TRIGGER_KINDS
    await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=True, allow_paused=allow_paused
    )
    result = await ProjectTriggerService(session).enqueue(
        project_id=project_id, kind=body.kind, payload=body.payload
    )
    await session.commit()
    return result


@router.get("/projects/{project_id}/triggers")
async def list_triggers(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=False
    )
    items = await ProjectTriggerService(session).list_for_project(project_id=project_id)
    return {"items": items}


@router.get("/projects/{project_id}/attachments")
async def list_attachments(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    items = await ProjectAttachmentService(session).list_for_project(
        project_id=project_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.post("/projects/{project_id}/attachments", status_code=201)
async def upload_attachment(
    project_id: str,
    body: AttachmentBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectAttachmentService(session).upload_base64(
        project_id=project_id,
        filename=body.filename,
        content_base64=body.content_base64,
        content_type=body.content_type,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}/attachments/{attachment_id}")
async def delete_attachment(
    project_id: str,
    attachment_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectAttachmentService(session).delete(
        project_id=project_id,
        attachment_id=attachment_id,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/attachments/{attachment_id}/content")
async def download_attachment_content(
    project_id: str,
    attachment_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> Response:
    raw, content_type, filename = await ProjectAttachmentService(session).read_content(
        project_id=project_id,
        attachment_id=attachment_id,
        principal=principal,
        employee=employee,
    )
    safe_name = filename.replace('"', "")
    return Response(
        content=raw,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.post("/projects/{project_id}/webhooks/http", status_code=202)
async def ingress_signed_webhook(
    project_id: str,
    request: Request,
    session: SessionDep,
    x_prodavan_signature: Annotated[str | None, Header(alias="X-Prodavan-Signature")] = None,
) -> dict:
    """External webhook.http ingress — HMAC-SHA256 over raw body (company policy secret)."""
    await _enforce_ingress_rate_limit(project_id, channel="webhook")
    project = await ProjectAccessPolicy(session).get_project(project_id)
    companies = AdminCompanyService(session)
    webhook_secret, _ = await companies.get_ingress_hmac_secrets(project.company_id)
    return await enqueue_signed_trigger(
        session,
        project_id=project_id,
        kind="webhook.http",
        raw_body=await request.body(),
        signature_header=x_prodavan_signature,
        secret=webhook_secret,
        secret_name="webhook_hmac_secret",
    )


@router.post("/projects/{project_id}/webhooks/telegram", status_code=202)
async def ingress_signed_telegram(
    project_id: str,
    request: Request,
    session: SessionDep,
    x_prodavan_signature: Annotated[str | None, Header(alias="X-Prodavan-Signature")] = None,
) -> dict:
    """Telegram bot transport ingress — HMAC-SHA256 (company telegram_hmac_secret)."""
    await _enforce_ingress_rate_limit(project_id, channel="telegram")
    project = await ProjectAccessPolicy(session).get_project(project_id)
    companies = AdminCompanyService(session)
    _, telegram_secret = await companies.get_ingress_hmac_secrets(project.company_id)
    return await enqueue_signed_trigger(
        session,
        project_id=project_id,
        kind="telegram.message",
        raw_body=await request.body(),
        signature_header=x_prodavan_signature,
        secret=telegram_secret,
        secret_name="telegram_hmac_secret",
    )


async def _enforce_ingress_rate_limit(project_id: str, *, channel: str) -> None:
    from prodavan.api.rate_limit import enforce_rate_limit
    from prodavan.config.settings import settings
    from prodavan.core.infra.cache import cache_key

    await enforce_rate_limit(
        cache_key("rl", "ingress", channel, project_id),
        limit=int(settings.ingress_rate_limit_per_minute or 0),
        detail=f"{channel} ingress rate limit exceeded",
    )
