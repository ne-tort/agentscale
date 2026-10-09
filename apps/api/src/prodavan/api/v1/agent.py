"""Agent HTTP surface (L08)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from prodavan.api.agent_auth import AgentAuthDep
from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.agent import AgentSessionService, AgentTriggerDispatcher
from prodavan.domain.errors import AppError
from prodavan.domain.projects import CHAT_MAX_ATTACHMENTS_PER_MESSAGE, CHAT_MAX_MESSAGE_CHARS
from prodavan.domain.projects.chat_error_policy import (
    normalize_chat_error_policy,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])

# SSE keepalive cadence for /chat/stream: L4 relays (punnel) silently cut
# idle streams, and without any frame the client cannot tell "thinking"
# from "wedged". Emitted only between events (never mid-turn data).
_CHAT_STREAM_KEEPALIVE_SEC = 15.0


class SendMessageBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(default="", max_length=CHAT_MAX_MESSAGE_CHARS)
    attachment_refs: list[str] = Field(default_factory=list, max_length=CHAT_MAX_ATTACHMENTS_PER_MESSAGE)
    model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> SendMessageBody:
        if not self.text.strip() and not self.attachment_refs:
            raise ValueError("text or attachment_refs required")
        return self


class CreateSessionBody(BaseModel):
    model_config = {"extra": "forbid"}

    model: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=200)


class ChatTurnBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(default="", max_length=CHAT_MAX_MESSAGE_CHARS)
    session_id: str | None = Field(default=None, max_length=64)
    attachment_refs: list[str] = Field(default_factory=list, max_length=CHAT_MAX_ATTACHMENTS_PER_MESSAGE)
    model: str | None = Field(default=None, max_length=128)
    # Client-generated idempotency key: a retried send re-attaches to the same
    # autonomous turn instead of starting a duplicate.
    turn_id: str | None = Field(default=None, max_length=64)

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


class ChatErrorPolicyBody(BaseModel):
    """Per-project chat reconnect policy (PUT — partial fields keep defaults)."""

    model_config = {"extra": "forbid"}

    interval_sec: int | None = Field(default=None, ge=1, le=3600)
    max_attempts: int | None = Field(default=None, ge=0, le=1000)
    fallback_models: list[str] | None = Field(default=None, max_length=20)


EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


def _chat_text(text: str, attachment_refs: list[str]) -> str:
    """Keep user text as-is; empty is fine when attachments are present."""
    _ = attachment_refs
    return text.strip()


@router.get("/projects/{project_id}/chat-error-policy")
async def get_chat_error_policy(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Effective chat reconnect policy (stored value or server default)."""
    from prodavan.application.project_service import ProjectAccessPolicy

    row = await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=False,
        allow_paused=True,
    )
    return normalize_chat_error_policy(row.chat_error_policy)


@router.put("/projects/{project_id}/chat-error-policy")
async def put_chat_error_policy(
    project_id: str,
    body: ChatErrorPolicyBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Store the chat reconnect policy (validated; partial fields keep defaults)."""
    from prodavan.application.project_service import ProjectAccessPolicy

    row = await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=employee,
        write=True,
        allow_paused=True,
    )
    fields = body.model_dump(exclude_unset=True)
    # None resets a field to the server default.
    fields = {k: v for k, v in fields.items() if v is not None}
    policy = normalize_chat_error_policy(fields)
    row.chat_error_policy = policy
    await session.commit()
    return policy


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
        title=body.title,
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


@router.get("/projects/{project_id}/agent/sessions/{session_id}")
async def get_agent_session(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).get_session_public(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


class PatchSessionBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, max_length=200)
    pin: bool | None = None


@router.patch("/projects/{project_id}/agent/sessions/{session_id}")
async def patch_agent_session(
    project_id: str,
    session_id: str,
    body: PatchSessionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    return await AgentSessionService(session).patch_session(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
        title=body.title,
        pin=body.pin,
    )


@router.delete("/projects/{project_id}/agent/sessions/{session_id}")
async def delete_agent_session(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Delete one chat dialog (session + history). Allowed while project paused."""
    return await AgentSessionService(session).delete_session(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


class ComposerDraftBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(default="", max_length=CHAT_MAX_MESSAGE_CHARS)


@router.get("/projects/{project_id}/agent/sessions/{session_id}/composer-draft")
async def get_session_composer_draft(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    return await ComposerDraftService(session).get_session_draft(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


@router.put("/projects/{project_id}/agent/sessions/{session_id}/composer-draft")
async def put_session_composer_draft(
    project_id: str,
    session_id: str,
    body: ComposerDraftBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    return await ComposerDraftService(session).put_session_draft(
        project_id=project_id,
        session_id=session_id,
        text=body.text,
        principal=principal,
        employee=employee,
    )


@router.delete("/projects/{project_id}/agent/sessions/{session_id}/composer-draft")
async def delete_session_composer_draft(
    project_id: str,
    session_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    return await ComposerDraftService(session).clear_session_draft(
        project_id=project_id,
        session_id=session_id,
        principal=principal,
        employee=employee,
    )


@router.get("/projects/{project_id}/composer-draft")
async def get_project_pending_composer_draft(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    return await ComposerDraftService(session).get_pending_draft(
        project_id=project_id,
        principal=principal,
        employee=employee,
    )


@router.put("/projects/{project_id}/composer-draft")
async def put_project_pending_composer_draft(
    project_id: str,
    body: ComposerDraftBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    """Upsert pending new-chat draft; materializes a session when text is meaningful (≥5)."""
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    svc = AgentSessionService(session)
    return await ComposerDraftService(session).put_pending_draft(
        project_id=project_id,
        text=body.text,
        principal=principal,
        employee=employee,
        create_session=svc.create_session,
    )


@router.delete("/projects/{project_id}/composer-draft")
async def delete_project_pending_composer_draft(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    from prodavan.application.agent.composer_draft_service import ComposerDraftService

    return await ComposerDraftService(session).clear_pending_draft(
        project_id=project_id,
        principal=principal,
        employee=employee,
    )


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
        iterator = svc.iter_chat_turn_sse(
            project_id=project_id,
            text=_chat_text(body.text, body.attachment_refs),
            session_id=body.session_id,
            attachment_refs=body.attachment_refs,
            principal=principal,
            employee=employee,
            model=body.model,
            turn_id=body.turn_id,
        ).__aiter__()
        pending: asyncio.Task | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(iterator.__anext__())
                # asyncio.wait (NOT wait_for): cancelling an in-flight
                # __anext__ would tear the generator mid-DB-flush. We just
                # stop waiting and emit a keepalive comment instead.
                done, _ = await asyncio.wait({pending}, timeout=_CHAT_STREAM_KEEPALIVE_SEC)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                task = pending
                pending = None
                try:
                    event = task.result()
                except StopAsyncIteration:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except AppError as err:
            payload = {
                "type": "_error",
                "data": {"code": err.code, "title": err.title, "detail": err.detail, "status": err.status},
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            # Never drop the TCP connection without a structured frame —
            # the client would otherwise sit in "streaming" forever.
            logger.exception("chat stream failed project=%s", project_id)
            payload = {
                "type": "_error",
                "data": {
                    "code": "INTERNAL",
                    "title": "Internal error",
                    "detail": str(exc)[:300],
                    "status": 500,
                },
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/projects/{project_id}/chat/transcript")
async def project_chat_transcript(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    session_id: str,
    limit: int = 500,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> dict:
    """Reload typed chat blocks for an explicit session (multi-chat).

    ``after_seq`` switches to incremental tail mode (events newer than the
    cursor) for live polling; the payload also carries ``turn_in_progress``
    so a reloaded client knows the agent is still working.
    """
    # Clamp to a sane upper bound — the default page is large so a typical
    # session shows its full history without the user scrolling up to trigger
    # pagination, but remains bounded for very long sessions.
    limit = max(1, min(limit, 500))
    return await AgentSessionService(session).get_transcript(
        project_id=project_id,
        principal=principal,
        employee=employee,
        session_id=session_id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
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
    from prodavan.api.agent_auth import require_bridge_project
    from prodavan.application.pod_identity.bridge import SCOPE_AGENT_EVENTS

    if auth.bridge is not None:
        require_bridge_project(auth.bridge, project_id)
        auth.bridge.require_scope(SCOPE_AGENT_EVENTS)
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
    from prodavan.api.agent_auth import require_bridge_project
    from prodavan.application.pod_identity.bridge import SCOPE_AGENT_EVENTS

    if auth.bridge is not None:
        require_bridge_project(auth.bridge, project_id)
        auth.bridge.require_scope(SCOPE_AGENT_EVENTS)
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
    from prodavan.api.agent_auth import require_bridge_project
    from prodavan.application.pod_identity.bridge import SCOPE_AGENT_EVENTS

    if auth.bridge is not None:
        require_bridge_project(auth.bridge, project_id)
        auth.bridge.require_scope(SCOPE_AGENT_EVENTS)
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
