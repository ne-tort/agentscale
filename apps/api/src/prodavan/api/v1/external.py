"""External Chat API (`/api/v1/external/...`) — machine access via app keys.

Auth: `Authorization: Bearer ak_<secret>` (created by an employee in cabinet
settings). The key acts as its bound employee (cabinet member), scoped to its
company (+ optional cabinet). Scopes gate the surface: chat:read / chat:write /
catalog:read.

Human OIDC Bearer tokens also work (the dep falls through to the employee's
principal), so the same surface serves apps and people.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.deps import get_db_session
from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.external.app_keys import ApiAppKeyService, ExternalAppContext
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external", tags=["external"])

SessionDep = Depends(get_db_session)


class _ExternalCtx:
    """Resolved caller: the app key context + the employee it acts as."""

    def __init__(
        self, *, app: ExternalAppContext, employee: EmployeeRow, principal: Principal
    ) -> None:
        self.app = app
        self.employee = employee
        self.principal = principal


async def _external_context(
    session: AsyncSession = SessionDep,
    authorization: str | None = Header(default=None),
) -> _ExternalCtx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="Bearer app key required")
    token = authorization.split(None, 1)[1].strip()
    app = await ApiAppKeyService(session).resolve(token)
    emp_q = await session.execute(
        select(EmployeeRow).where(EmployeeRow.id == app.employee_id)
    )
    employee = emp_q.scalars().first()
    if employee is None or employee.deleted_at is not None or employee.status == "disabled":
        raise AppError(code="UNAUTHORIZED", title="Unauthorized", status=401, detail="employee not active")
    principal = Principal(
        sub=employee.keycloak_sub or employee.id,
        roles=frozenset({"employee"}),
        email=employee.email,
        username=employee.login,
    )
    return _ExternalCtx(app=app, employee=employee, principal=principal)


ExternalDep = Depends(_external_context)


def _require_scope(ctx: _ExternalCtx, scope: str) -> None:
    if scope not in ctx.app.scopes:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail=f"app key missing scope: {scope}",
        )


async def _cabinet_visible(ctx: _ExternalCtx, session: AsyncSession, cabinet_id: str) -> CabinetInstanceRow:
    """The key may touch a cabinet if: within its cabinet scope AND the employee
    belongs to it (assignment) AND the cabinet is the key's company."""
    if ctx.app.cabinet_id and ctx.app.cabinet_id != cabinet_id:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
    inst = await session.get(CabinetInstanceRow, cabinet_id)
    if inst is None or inst.company_id != ctx.app.company_id:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
    q = await session.execute(
        select(CabinetEmployeeAssignmentRow).where(
            CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
            CabinetEmployeeAssignmentRow.employee_id == ctx.employee.id,
        )
    )
    if q.scalars().first() is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="not a cabinet member")
    return inst


async def _project_visible(ctx: _ExternalCtx, session: AsyncSession, project_id: str) -> ProjectRow:
    project = await session.get(ProjectRow, project_id)
    if project is None or project.company_id != ctx.app.company_id:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
    await _cabinet_visible(ctx, session, project.cabinet_id)
    return project


# ------------------------------------------------------------------ cabinets

@router.get("/cabinets")
async def list_cabinets(
    ctx: _ExternalCtx = ExternalDep, session: AsyncSession = SessionDep
) -> dict[str, Any]:
    q = (
        select(CabinetInstanceRow)
        .join(
            CabinetEmployeeAssignmentRow,
            CabinetEmployeeAssignmentRow.cabinet_id == CabinetInstanceRow.id,
        )
        .where(
            CabinetInstanceRow.company_id == ctx.app.company_id,
            CabinetEmployeeAssignmentRow.employee_id == ctx.employee.id,
        )
    )
    if ctx.app.cabinet_id:
        q = q.where(CabinetInstanceRow.id == ctx.app.cabinet_id)
    result = await session.execute(q)
    return {
        "items": [
            {"id": c.id, "name": c.name, "status": str(c.status)}
            for c in result.scalars().all()
        ]
    }


@router.get("/cabinets/{cabinet_id}/projects")
async def list_projects(
    cabinet_id: str,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    await _cabinet_visible(ctx, session, cabinet_id)
    result = await session.execute(
        select(ProjectRow).where(ProjectRow.cabinet_id == cabinet_id)
    )
    return {
        "items": [
            {"id": p.id, "name": p.name, "slug": p.slug, "status": str(p.status)}
            for p in result.scalars().all()
        ]
    }


# ------------------------------------------------------------------ chats

@router.get("/projects/{project_id}/chats")
async def list_chats(
    project_id: str,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    _require_scope(ctx, "chat:read")
    await _project_visible(ctx, session, project_id)
    result = await session.execute(
        select(AgentSessionRow)
        .where(AgentSessionRow.project_id == project_id)
        .order_by(AgentSessionRow.created_at.desc())
    )
    return {
        "items": [
            {
                "session_id": s.id,
                "title": s.title,
                "status": str(s.status),
                "model": s.model,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in result.scalars().all()
        ]
    }


class CreateChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=128)


@router.post("/projects/{project_id}/chats", status_code=201)
async def create_chat(
    project_id: str,
    body: CreateChatBody,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    _require_scope(ctx, "chat:write")
    await _project_visible(ctx, session, project_id)
    return await AgentSessionService(session).create_session(
        project_id=project_id,
        principal=ctx.principal,
        employee=ctx.employee,
        model=body.model,
        title=body.title,
    )


@router.get("/chats/{session_id}")
async def get_chat(
    session_id: str,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    _require_scope(ctx, "chat:read")
    row = await session.get(AgentSessionRow, session_id)
    if row is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="chat not found")
    await _project_visible(ctx, session, row.project_id)
    return await AgentSessionService(session).get_session_public(
        project_id=row.project_id,
        session_id=session_id,
        principal=ctx.principal,
        employee=ctx.employee,
    )


class SendMessageBody(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(min_length=1, max_length=20000)
    wait: bool = False
    model: str | None = Field(default=None, max_length=128)


@router.post("/chats/{session_id}/messages", status_code=201)
async def send_message(
    session_id: str,
    body: SendMessageBody,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Send a message; wait=true blocks until the turn completes."""
    _require_scope(ctx, "chat:write")
    row = await session.get(AgentSessionRow, session_id)
    if row is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="chat not found")
    await _project_visible(ctx, session, row.project_id)
    svc = AgentSessionService(session)
    if body.wait:
        result = await svc.send_message(
            project_id=row.project_id,
            session_id=session_id,
            text=body.text,
            attachment_refs=None,
            principal=ctx.principal,
            employee=ctx.employee,
            model=body.model,
        )
        return {"session_id": session_id, "wait": True, **result}
    # async: detached turn + poll via /messages
    turn = await svc.start_detached_turn(
        project_id=row.project_id,
        session_id=session_id,
        text=body.text,
        attachment_refs=None,
        principal=ctx.principal,
        employee=ctx.employee,
        model=body.model,
    )
    # DetachedTurn несёт asyncio.Task — отдаём только идентификаторы
    return {
        "session_id": session_id,
        "wait": False,
        "turn_id": getattr(turn, "turn_id", None),
    }


@router.get("/chats/{session_id}/messages")
async def list_messages(
    session_id: str,
    after_seq: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Incremental transcript: pass after_seq to get only new events."""
    _require_scope(ctx, "chat:read")
    row = await session.get(AgentSessionRow, session_id)
    if row is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="chat not found")
    await _project_visible(ctx, session, row.project_id)
    return await AgentSessionService(session).get_transcript(
        project_id=row.project_id,
        principal=ctx.principal,
        employee=ctx.employee,
        session_id=session_id,
        limit=limit,
        after_seq=after_seq,
    )


@router.get("/chats/{session_id}/answer")
async def latest_answer(
    session_id: str,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Latest assistant text + whether a turn is in progress."""
    _require_scope(ctx, "chat:read")
    row = await session.get(AgentSessionRow, session_id)
    if row is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="chat not found")
    await _project_visible(ctx, session, row.project_id)
    transcript = await AgentSessionService(session).get_transcript(
        project_id=row.project_id,
        principal=ctx.principal,
        employee=ctx.employee,
        session_id=session_id,
        limit=500,
    )
    text = ""
    for block in reversed(transcript.get("blocks") or []):
        if block.get("kind") == "assistant_markdown" and str(block.get("text") or "").strip():
            text = str(block["text"]).strip()
            break
    return {
        "session_id": session_id,
        "assistant_text": text,
        "turn_in_progress": bool(transcript.get("turn_in_progress")),
    }


# ------------------------------------------------------------------ тендеры (WAVE15)
# Внешний intake: создать тендер из внешнего источника (b2b-center и т.п.) и
# при желании сразу взять в работу — чат создаётся, позиции копируются, агент
# получает стартовое сообщение. Скоупы: tenders:write / chat:write.

_TENDER_STATUSES_EXT = frozenset(
    {"draft", "active", "submitted", "won", "lost", "cancelled", "irrelevant"}
)


class ExternalTenderLine(BaseModel):
    model_config = {"extra": "forbid"}
    id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=1000)
    part_number: str | None = Field(default=None, max_length=200)
    qty: float | None = Field(default=None, gt=0)


class ExternalTenderBody(BaseModel):
    """Тендер из внешнего источника; обязательный title, позиции в lines."""

    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1, max_length=500)
    customer: str | None = Field(default=None, max_length=500)
    deadline_at: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=8000)
    source_url: str | None = Field(default=None, max_length=2000)
    source_ref: str | None = Field(default=None, max_length=200)
    lines: list[ExternalTenderLine] = Field(default_factory=list, max_length=500)
    # сразу взять в работу: создать чат и импортировать позиции
    attach: bool = False
    chat_title: str | None = Field(default=None, max_length=200)
    kickoff: bool = True  # стартовое сообщение агенту после attach


def _ext_lines_json(lines: list[ExternalTenderLine]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, line in enumerate(lines):
        lid = (line.id or "").strip() or f"tl{idx + 1}"
        if lid in seen:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"duplicate line id: {lid}",
            )
        seen.add(lid)
        out.append(
            {
                "id": lid,
                "title": line.title.strip(),
                "part_number": (line.part_number or "").strip(),
                "qty": line.qty if line.qty is not None else 1,
            }
        )
    return out


@router.post("/projects/{project_id}/tenders", status_code=201)
async def create_external_tender(
    project_id: str,
    body: ExternalTenderBody,
    ctx: _ExternalCtx = ExternalDep,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Создать тендер из внешнего источника (и опционально сразу взять в работу).

    attach=true: создаётся чат (chat_title или «Тендер: {title}»), позиции
    копируются в request_lines чата (tender_attach, идемпотентно), при
    kickoff=true агенту уходит стартовое сообщение (detached turn).
    """
    _require_scope(ctx, "chat:write")
    project = await _project_visible(ctx, session, project_id)
    status = (body.status or "active").strip()
    if status not in _TENDER_STATUSES_EXT:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"status must be one of {sorted(_TENDER_STATUSES_EXT)}",
        )
    deadline = (body.deadline_at or "").strip()
    if deadline:
        from datetime import datetime as _dt

        try:
            _dt.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="deadline_at must be ISO-8601",
            ) from None

    from prodavan.application.modules.equipment_offers_service import ModuleRowIO
    from prodavan.application.modules.module_action_executor import ModuleActionExecutor

    io = ModuleRowIO(
        session,
        cabinet_id=project.cabinet_id,
        project_id=project_id,
        principal=ctx.principal,
        employee=ctx.employee,
        session_id=None,
    )
    tender_body: dict[str, Any] = {
        "title": body.title.strip(),
        "customer": (body.customer or "").strip(),
        "status": status,
        "comment": (body.comment or "").strip(),
        "source_url": (body.source_url or "").strip(),
        "source_ref": (body.source_ref or "").strip(),
        "lines_json": _ext_lines_json(body.lines),
    }
    if deadline:
        tender_body["deadline_at"] = deadline
    created = await io.create("tenders", tender_body)
    tender_id = str(created.get("row_id") or "")

    result: dict[str, Any] = {"tender_id": tender_id, "attached": False}
    if not body.attach:
        return result

    chat_title = (body.chat_title or "").strip() or f"Тендер: {body.title.strip()[:120]}"
    chat = await AgentSessionService(session).create_session(
        project_id=project_id,
        principal=ctx.principal,
        employee=ctx.employee,
        model=None,
        title=chat_title,
    )
    session_id = str(chat.get("id") or chat.get("session_id") or "")
    attach = await ModuleActionExecutor(session).invoke(
        cabinet_id=project.cabinet_id,
        module_id="mod_equipment",
        action_id="tender_attach",
        principal=ctx.principal,
        employee=ctx.employee,
        row_id=tender_id,
        project_id=project_id,
        session_id=session_id,
    )
    result.update(
        {
            "attached": True,
            "session_id": session_id,
            "attach": {k: attach.get(k) for k in ("created", "updated", "lines_total")},
        }
    )
    if body.kickoff:
        kickoff_text = (
            f"В работу взят тендер «{body.title.strip()}»"
            + (f" (заказчик: {body.customer.strip()})" if body.customer else "")
            + ". Его позиции уже импортированы в позиции заказчика этого чата. "
            "Сначала оцени релевантность: если тендер не про компьютерную/"
            "электронную технику или не наш профиль — отбракуй его "
            "(tender_upsert: status=irrelevant, relevance_note с причиной) и "
            "остановись. Иначе подбери кандидатов по каждой позиции по правилам "
            "модуля (поиск в каталогах, found_groups_upsert)."
        )
        turn = await AgentSessionService(session).start_detached_turn(
            project_id=project_id,
            session_id=session_id,
            text=kickoff_text,
            attachment_refs=None,
            principal=ctx.principal,
            employee=ctx.employee,
            model=None,
        )
        result["kickoff_turn_id"] = getattr(turn, "turn_id", None)
    return result


# ------------------------------------------------------------------ управление
# Создание ключей и подписок вебхуков — от человека (OIDC), не от app key.
# Компания-scope: участник компании (сотрудник) управляет своими ключами.

from prodavan.api.deps import PrincipalDep  # noqa: E402
from prodavan.api.deps import get_current_employee  # noqa: E402
from prodavan.application.external.webhooks import WebhookService  # noqa: E402

_EmployeeDep = Depends(get_current_employee)


def _require_employee(employee) -> None:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")


async def _employee_company_id(session: AsyncSession, employee: EmployeeRow) -> str:
    """Компания сотрудника через membership (первая активная)."""
    from prodavan.infrastructure.persistence.models.identity import MembershipRow
    q = await session.execute(
        select(MembershipRow.company_id).where(
            MembershipRow.employee_id == employee.id
        )
    )
    cid = q.scalars().first()
    if not cid:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee has no company")
    return str(cid)


class CreateAppKeyBody(BaseModel):
    model_config = {"extra": "forbid"}
    label: str = Field(min_length=1, max_length=200)
    cabinet_id: str | None = None
    scopes: list[str] | None = None
    expires_at: str | None = None


@router.post("/app-keys", status_code=201)
async def create_app_key(
    body: CreateAppKeyBody,
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
) -> dict:
    _require_employee(employee)
    expires = None
    if body.expires_at:
        from datetime import datetime as _dt
        try:
            expires = _dt.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad expires_at")
    row, token = await ApiAppKeyService(session).create(
        label=body.label,
        company_id=await _employee_company_id(session, employee),
        cabinet_id=body.cabinet_id,
        employee_id=employee.id,
        scopes=body.scopes,
        expires_at=expires,
        created_by=employee.id,
    )
    await session.commit()
    return {
        "id": row.id,
        "label": row.label,
        "token": token,  # показывается один раз
        "token_prefix": row.token_prefix,
        "scopes": row.scopes,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


@router.get("/app-keys")
async def list_app_keys(
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
) -> dict:
    _require_employee(employee)
    company_id = await _employee_company_id(session, employee)
    rows = await ApiAppKeyService(session).list_for_company(company_id)
    return {
        "items": [
            {
                "id": r.id,
                "label": r.label,
                "token_prefix": r.token_prefix,
                "scopes": r.scopes,
                "cabinet_id": r.cabinet_id,
                "status": r.status,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.delete("/app-keys/{key_id}")
async def revoke_app_key(
    key_id: str,
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
) -> dict:
    _require_employee(employee)
    company_id = await _employee_company_id(session, employee)
    ok = await ApiAppKeyService(session).revoke(key_id, company_id=company_id)
    if not ok:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="key not found")
    await session.commit()
    return {"ok": True}


class CreateWebhookBody(BaseModel):
    model_config = {"extra": "forbid"}
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(min_length=1)
    project_id: str | None = None


@router.post("/webhooks", status_code=201)
async def create_webhook(
    body: CreateWebhookBody,
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
) -> dict:
    _require_employee(employee)
    company_id = await _employee_company_id(session, employee)
    row, secret = await WebhookService(session).subscribe(
        company_id=company_id,
        url=body.url,
        events=body.events,
        project_id=body.project_id,
        created_by=employee.id,
    )
    await session.commit()
    return {
        "id": row.id,
        "url": row.url,
        "events": row.events,
        "project_id": row.project_id,
        "secret": secret,  # показывается один раз — им подписываются тела
    }


@router.get("/webhooks")
async def list_webhooks(
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
    project_id: str | None = Query(default=None),
) -> dict:
    _require_employee(employee)
    company_id = await _employee_company_id(session, employee)
    rows = await WebhookService(session).list_for_company(company_id, project_id=project_id)
    return {
        "items": [
            {
                "id": r.id,
                "url": r.url,
                "events": r.events,
                "project_id": r.project_id,
                "enabled": r.enabled,
                "fail_count": r.fail_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.delete("/webhooks/{subscription_id}")
async def delete_webhook(
    subscription_id: str,
    principal: PrincipalDep,
    session: AsyncSession = SessionDep,
    employee: EmployeeRow | None = _EmployeeDep,
) -> dict:
    _require_employee(employee)
    company_id = await _employee_company_id(session, employee)
    ok = await WebhookService(session).delete(subscription_id, company_id=company_id)
    if not ok:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="subscription not found")
    await session.commit()
    return {"ok": True}
