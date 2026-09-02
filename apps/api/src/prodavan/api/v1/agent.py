"""Agent HTTP surface (L08)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from prodavan.api.agent_auth import AgentAuthDep
from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.agent import AgentSessionService, AgentTriggerDispatcher
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["agent"])


class SendMessageBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(default="")
    attachment_refs: list[str] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> SendMessageBody:
        if not self.text.strip() and not self.attachment_refs:
            raise ValueError("text or attachment_refs required")
        return self


class CreateSessionBody(BaseModel):
    model_config = {"extra": "forbid"}

    model: str | None = Field(default=None, max_length=128)


class ChatTurnBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(default="")
    session_id: str | None = Field(default=None, max_length=64)
    attachment_refs: list[str] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> ChatTurnBody:
        if not self.text.strip() and not self.attachment_refs:
            raise ValueError("text or attachment_refs required")
        return self


class ToolApprovalBody(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, max_length=64)
    decision: str = Field(pattern="^(approve|deny)$")


class AppendAgentEventBody(BaseModel):
    model_config = {"extra": "forbid"}

    type: str = Field(min_length=1, max_length=64)
    data: dict = Field(default_factory=dict)
    at: str | None = Field(default=None, max_length=64)


EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


def _chat_text(text: str, attachment_refs: list[str]) -> str:
    trimmed = text.strip()
    if trimmed:
        return trimmed
    if attachment_refs:
        return "(attachment)"
    return ""


@router.post("/projects/{project_id}/agent/sessions", status_code=201)
async def create_agent_session(
    project_id: str,
    body: CreateSessionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await AgentSessionService(session).create_session(
        project_id=project_id,
        principal=principal,
        employee=employee,
        model=body.model,
    )


@router.get("/projects/{project_id}/agent/sessions")
async def list_agent_sessions(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    items = await AgentSessionService(session).list_sessions(
        project_id=project_id, principal=principal, employee=employee
    )
    return {"items": items}


@router.post("/projects/{project_id}/chat")
async def project_chat_turn(
    project_id: str,
    body: ChatTurnBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Convenience chat turn for mobile shell — session reuse + assistant_text (L05/L09)."""
    return await AgentSessionService(session).chat_turn(
        project_id=project_id,
        text=_chat_text(body.text, body.attachment_refs),
        session_id=body.session_id,
        attachment_refs=body.attachment_refs,
        principal=principal,
        employee=employee,
        model=body.model,
    )


@router.post("/projects/{project_id}/chat/stream")
async def project_chat_stream(
    project_id: str,
    body: ChatTurnBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> StreamingResponse:
    """SSE chat turn — text_delta chunks for L05 workspace streaming."""

    async def generate():
        svc = AgentSessionService(session)
        try:
            async for event in svc.iter_chat_turn_sse(
                project_id=project_id,
                text=_chat_text(body.text, body.attachment_refs),
                session_id=body.session_id,
                attachment_refs=body.attachment_refs,
                principal=principal,
                employee=employee,
                model=body.model,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except AppError as err:
            payload = {
                "type": "_error",
                "data": {"code": err.code, "title": err.title, "detail": err.detail, "status": err.status},
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/projects/{project_id}/chat/transcript")
async def project_chat_transcript(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    session_id: str | None = None,
) -> dict:
    """Reload chat bubbles for workspace (L05)."""
    return await AgentSessionService(session).get_transcript(
        project_id=project_id,
        principal=principal,
        employee=employee,
        session_id=session_id,
    )


@router.post("/projects/{project_id}/agent/sessions/{session_id}/send")
async def send_agent_message(
    project_id: str,
    session_id: str,
    body: SendMessageBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).send_message(
        project_id=project_id,
        session_id=session_id,
        text=_chat_text(body.text, body.attachment_refs),
        attachment_refs=body.attachment_refs,
        principal=principal,
        employee=employee,
        model=body.model,
    )


@router.get("/projects/{project_id}/agent/sessions/{session_id}/events")
async def list_agent_events(
    project_id: str,
    session_id: str,
    auth: AgentAuthDep,
    session: SessionDep,
) -> dict:
    items = await AgentSessionService(session).list_events(
        session_id=session_id,
        project_id=project_id,
        principal=auth.principal,
        employee=auth.employee,
        pod_agent=auth.pod_agent,
    )
    return {"items": items}


@router.post("/projects/{project_id}/agent/sessions/{session_id}/events", status_code=201)
async def append_agent_event(
    project_id: str,
    session_id: str,
    body: AppendAgentEventBody,
    auth: AgentAuthDep,
    session: SessionDep,
) -> dict:
    """Hybrid transcript write from OpenClaw bridge (L03 dual-write)."""
    return await AgentSessionService(session).append_event(
        project_id=project_id,
        session_id=session_id,
        event_type=body.type,
        data=body.data,
        at=body.at,
        principal=auth.principal,
        employee=auth.employee,
        pod_agent=auth.pod_agent,
    )


@router.get("/projects/{project_id}/agent/sessions/{session_id}/transcript")
async def session_transcript(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).get_transcript(
        project_id=project_id,
        principal=principal,
        employee=employee,
        session_id=session_id,
    )


@router.post("/projects/{project_id}/agent/sessions/{session_id}/cancel")
async def cancel_agent_session(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).cancel_session(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/agent/sessions/{session_id}/pending-approvals")
async def list_pending_tool_approvals(
    project_id: str,
    session_id: str,
    auth: AgentAuthDep,
    session: SessionDep,
) -> dict:
    items = await AgentSessionService(session).list_pending_approvals(
        project_id=project_id,
        session_id=session_id,
        principal=auth.principal,
        employee=auth.employee,
        pod_agent=auth.pod_agent,
    )
    return {"items": items}


@router.post("/projects/{project_id}/agent/sessions/{session_id}/tool-approvals")
async def resolve_tool_approval(
    project_id: str,
    session_id: str,
    body: ToolApprovalBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).resolve_tool_approval(
        project_id=project_id,
        session_id=session_id,
        approval_id=body.id,
        decision=body.decision,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/agent/sessions/{session_id}/fork", status_code=201)
async def fork_agent_session(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return await AgentSessionService(session).fork_session(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/agent/sessions/{session_id}/sidechains/{tool_use_id}/transcript")
async def get_sidechain_transcript(
    project_id: str,
    session_id: str,
    tool_use_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).get_sidechain_transcript(
        project_id=project_id,
        session_id=session_id,
        tool_use_id=tool_use_id,
        principal=principal,
        employee=employee,
    )


@router.post("/projects/{project_id}/triggers/dispatch")
async def dispatch_project_triggers(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    max: int = 1,
) -> dict:
    """Dispatch one trigger (max=1) or drain up to max queued triggers."""
    dispatcher = AgentTriggerDispatcher(session)
    if max <= 1:
        return await dispatcher.dispatch_next(
            project_id=project_id, principal=principal, employee=employee
        )
    return await dispatcher.dispatch_batch(
        project_id=project_id, principal=principal, employee=employee, max_n=max
    )
