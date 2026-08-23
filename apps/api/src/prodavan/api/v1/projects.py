"""Projects HTTP surface (L07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.projects import (
    ProjectAttachmentService,
    ProjectService,
    ProjectTriggerService,
)
from prodavan.application.projects.access import ProjectAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus, verify_webhook_signature
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
    agent_provider: str | None = Field(default=None, max_length=32)


class TriggerBody(BaseModel):
    model_config = {"extra": "forbid"}

    kind: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


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
    return await ProjectService(session).create(
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
    items = await ProjectService(session).list_for_cabinet(
        cabinet_id=cabinet_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectService(session).get(project_id=project_id, principal=principal, employee=employee)


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
    return await ProjectService(session).patch(
        project_id=project_id,
        principal=principal,
        employee=employee,
        name=fields.get("name"),
        agent_provider=fields.get("agent_provider"),
        update_agent_provider="agent_provider" in fields,
    )


@router.post("/projects/{project_id}/materialize")
async def rematerialize_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectService(session).rematerialize(
        project_id=project_id, principal=principal, employee=employee
    )


@router.post("/projects/{project_id}/pause")
async def pause_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectService(session).pause(project_id=project_id, principal=principal, employee=employee)


@router.post("/projects/{project_id}/resume")
async def resume_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectService(session).resume(project_id=project_id, principal=principal, employee=employee)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await ProjectService(session).delete(project_id=project_id, principal=principal, employee=employee)


@router.post("/projects/{project_id}/triggers", status_code=202)
async def post_trigger(
    project_id: str,
    body: TriggerBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.projects.access import ProjectAccessService

    await ProjectAccessService(session).require_access(
        project_id=project_id, principal=principal, employee=employee, write=True
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
    from prodavan.application.projects.access import ProjectAccessService

    await ProjectAccessService(session).require_access(
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


@router.post("/projects/{project_id}/webhooks/http", status_code=202)
async def ingress_signed_webhook(
    project_id: str,
    request: Request,
    session: SessionDep,
    x_prodavan_signature: Annotated[str | None, Header(alias="X-Prodavan-Signature")] = None,
) -> dict:
    """External webhook.http ingress — HMAC-SHA256 over raw body (company policy secret)."""
    project = await ProjectAccessService(session).get_project(project_id)
    if project.status == ProjectStatus.DELETED:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
    policy = await AdminCompanyService(session).get_agent_policy(project.company_id)
    secret = policy.webhook_hmac_secret
    if not secret:
        raise AppError(
            code="WEBHOOK_NOT_CONFIGURED",
            title="Webhook not configured",
            status=503,
            detail="company webhook_hmac_secret not set",
        )
    raw = await request.body()
    if not verify_webhook_signature(secret=secret, body=raw, header=x_prodavan_signature):
        raise AppError(
            code="WEBHOOK_SIGNATURE_INVALID",
            title="Invalid signature",
            status=401,
            detail="X-Prodavan-Signature mismatch",
        )
    import json

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be JSON object",
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be JSON object",
        )
    result = await ProjectTriggerService(session).enqueue(
        project_id=project_id, kind="webhook.http", payload=payload
    )
    await session.commit()
    return result
