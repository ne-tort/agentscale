"""Platform agent session API (framework only — tools come from cabinet pack)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from prodavan.api.deps import CabinetSession, get_cabinet_session
from prodavan.cabinets.registry import get_module_for_profile
from prodavan.cabinets.spi import SpiContext
from prodavan.infrastructure.persistence.models.tenants import Cabinet
from prodavan.platform.agent.sessions import AgentSessionStore

router = APIRouter(tags=["platform-agent"])
_store = AgentSessionStore()


class StartSessionRequest(BaseModel):
    project_id: str = Field(min_length=1)


class ChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post(
    "/projects/{project_id}/agent/sessions",
    status_code=status.HTTP_201_CREATED,
)
async def start_agent_session(
    project_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    result = await cs.session.execute(
        select(Cabinet).where(Cabinet.id == cs.ctx.cabinet_id)
    )
    cabinet = result.scalar_one_or_none()
    if cabinet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cabinet not found")
    module = get_module_for_profile(cabinet.profile_id)
    ctx = SpiContext(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        user_id=cs.ctx.user.user_id,
        project_id=project_id,
    )
    manifest = await module.manifest(ctx)
    tools = manifest.get("tools") or []
    session = _store.create(
        tenant_id=cs.ctx.user.tenant_id,
        cabinet_id=cs.ctx.cabinet_id,
        project_id=project_id,
        user_id=cs.ctx.user.user_id,
        allowed_tools=[t.get("name") for t in tools if t.get("allow")],
        pack_id=module.pack_id,
    )
    return session


@router.get("/projects/{project_id}/agent/sessions/{session_id}")
async def get_agent_session(
    project_id: str,
    session_id: str,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    session = _store.get(session_id)
    if session is None or session["project_id"] != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session["cabinet_id"] != str(cs.ctx.cabinet_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    return session


@router.post("/projects/{project_id}/agent/sessions/{session_id}/messages")
async def post_agent_message(
    project_id: str,
    session_id: str,
    body: ChatMessageRequest,
    cs: CabinetSession = Depends(get_cabinet_session),
) -> dict:
    """Platform chat stub: echoes + lists allowed cabinet tools (no LLM yet)."""
    session = _store.get(session_id)
    if session is None or session["project_id"] != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session["cabinet_id"] != str(cs.ctx.cabinet_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cabinet mismatch")
    reply = (
        f"[platform agent · {session['pack_id']}] "
        f"Получено: {body.text[:200]}. "
        f"Доступные tools кабинета: {', '.join(session['allowed_tools']) or 'нет'}."
    )
    message = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "text": reply,
        "at": datetime.now(UTC).isoformat(),
    }
    _store.append_message(session_id, {"role": "user", "text": body.text, "at": message["at"]})
    _store.append_message(session_id, message)
    return {"message": message, "session_id": session_id}