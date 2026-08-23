"""Agent HTTP surface (L08)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.agent import AgentSessionService, AgentTriggerDispatcher
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["agent"])


class SendMessageBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1)
    attachment_refs: list[str] = Field(default_factory=list)


class CreateSessionBody(BaseModel):
    model_config = {"extra": "forbid"}

    model: str | None = Field(default=None, max_length=128)


class ChatTurnBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1)
    session_id: str | None = Field(default=None, max_length=64)
    attachment_refs: list[str] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=128)


EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


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
        text=body.text,
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
                text=body.text,
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
        text=body.text,
        attachment_refs=body.attachment_refs,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/agent/sessions/{session_id}/events")
async def list_agent_events(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    items = await AgentSessionService(session).list_events(
        session_id=session_id,
        project_id=project_id,
        principal=principal,
        employee=employee,
    )
    return {"items": items}


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


@router.post("/projects/{project_id}/triggers/dispatch")
async def dispatch_project_triggers(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentTriggerDispatcher(session).dispatch_next(
        project_id=project_id, principal=principal, employee=employee
    )
