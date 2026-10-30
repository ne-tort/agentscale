"""Tender workflow: «Взять в работу» creates/finds the Тендеры project + chat.

This is the single backend path used by the tender row action, the API and MCP:
- find an existing project named "Тендеры" in the same cabinet;
- reload it once if it is in ERROR;
- if absent, require an available AI key and create/launch it;
- create (or reuse) a session, copy tender positions into request_lines and
  start a detached kickoff turn with the tender context;
- mark the tender `work_status=in_progress` and record the chat.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.session_service import AgentSessionService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.application.modules.equipment_offers_service import ModuleRowIO
from prodavan.application.project_service.command import ProjectCommand
from prodavan.config.settings import Settings
from prodavan.config.settings import settings as global_settings
from prodavan.domain.errors import AppError
from prodavan.domain.projects.types import ProjectStatus
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

TENDERS_TABLE = "tenders"
MODULE_ID = "mod_equipment"
_log = logging.getLogger(__name__)


class TenderWorkflowService:
    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or global_settings

    async def take_to_work(
        self,
        *,
        project_id: str,
        tender_id: str,
        principal: Any,
        employee: Any,
    ) -> dict[str, Any]:
        source = await self._session.get(ProjectRow, project_id)
        if source is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        io = ModuleRowIO(
            self._session,
            cabinet_id=source.cabinet_id,
            project_id=project_id,
            principal=principal,
            employee=employee,
            session_id=None,
        )
        rows = await io.list(TENDERS_TABLE)
        tender = next((r for r in rows if str(r.get("row_id")) == str(tender_id)), None)
        if tender is None:
            raise AppError(
                code="NOT_FOUND", title="Not Found", status=404, detail=f"tender not found: {tender_id}"
            )
        body = dict(tender.get("body") or {})
        target = await self._resolve_target_project(source=source, principal=principal, employee=employee)

        session_id = await self._existing_session(tender=body, target_id=target.id)
        created_session = False
        if not session_id:
            session_id = await self._create_session_with_retry(
                target=target, body=body, principal=principal, employee=employee
            )
            created_session = True

        attach = await self._attach(
            cabinet_id=target.cabinet_id,
            project_id=target.id,
            tender_id=str(tender_id),
            session_id=session_id,
            principal=principal,
            employee=employee,
        )
        await self._mark_taken(
            io=io,
            tender_id=str(tender_id),
            body=body,
            session_id=session_id,
            employee=employee,
        )
        kickoff_turn_id = None
        if created_session:
            kickoff_turn_id = await self._kickoff(
                project_id=target.id,
                session_id=session_id,
                body=body,
                principal=principal,
                employee=employee,
            )
        return {
            "ok": True,
            "tender_id": str(tender_id),
            "project_id": target.id,
            "project_name": target.name,
            "session_id": session_id,
            "created_project": not self._was_existing(target),
            "created_session": created_session,
            "attach": {k: attach.get(k) for k in ("created", "updated", "lines_total")},
            "kickoff_turn_id": kickoff_turn_id,
        }

    # ------------------------------------------------------------------ project
    def _was_existing(self, target: ProjectRow) -> bool:
        return bool(getattr(target, "_b2b_existing", False))

    async def _resolve_target_project(
        self, *, source: ProjectRow, principal: Any, employee: Any
    ) -> ProjectRow:
        name = (self._settings.tender_project_name or "Тендеры").strip() or "Тендеры"
        if (source.name or "").strip() == name:
            return source
        q = await self._session.execute(
            select(ProjectRow)
            .where(
                ProjectRow.cabinet_id == source.cabinet_id,
                ProjectRow.name == name,
                ProjectRow.status != ProjectStatus.DELETED,
            )
            .order_by(ProjectRow.created_at.asc())
            .limit(1)
        )
        target = q.scalar_one_or_none()
        if target is not None:
            setattr(target, "_b2b_existing", True)
            if target.status == ProjectStatus.ERROR:
                await self._reload_once(target=target, principal=principal, employee=employee)
            return target

        keys = await AiKeysService(self._session).list_available_keys_for_project(project=source)
        if not keys:
            raise AppError(
                code="NO_AI_KEY",
                title="Нет доступного AI-ключа",
                status=409,
                detail="Для создания проекта «Тендеры» нужен хотя бы один активный AI-ключ кабинета",
            )
        provider = str(keys[0].get("provider") or "").strip() or None
        created = await ProjectCommand(self._session).create(
            cabinet_id=source.cabinet_id,
            name=name,
            employee=employee,
            principal=principal,
            agent_provider=provider,
        )
        target_id = str(created.get("id") or created.get("project_id") or "")
        target = await self._session.get(ProjectRow, target_id)
        if target is None:
            raise AppError(code="INTERNAL", title="Project not created", status=500)
        # launch() requires resolved_ai_key_id; a newly created project has only
        # agent_provider. Bind the first available key explicitly.
        try:
            await ProjectCommand(self._session).patch(
                project_id=target.id,
                principal=principal,
                employee=employee,
                agent_provider=provider,
                update_agent_provider=True,
                resolved_ai_key_id=str(keys[0].get("id") or ""),
                update_resolved_ai_key_id=True,
            )
        except AppError:
            _log.exception("tender project key binding failed project_id=%s", target.id)
            raise
        try:
            await ProjectCommand(self._session).launch(
                project_id=target.id, principal=principal, employee=employee
            )
        except AppError as exc:
            if exc.code not in {"POD_ALREADY_EXISTS"}:
                await self._session.refresh(target)
                if target.status == ProjectStatus.ERROR:
                    await self._reload_once(target=target, principal=principal, employee=employee)
        await self._session.refresh(target)
        setattr(target, "_b2b_existing", False)
        return target

    async def _reload_once(self, *, target: ProjectRow, principal: Any, employee: Any) -> None:
        try:
            await ProjectCommand(self._session).reload_project(
                project_id=target.id, principal=principal, employee=employee
            )
        except Exception:
            _log.exception("tender project reload failed project_id=%s", target.id)
        await self._session.refresh(target)
        if target.status == ProjectStatus.ERROR:
            raise AppError(
                code="PROJECT_ERROR",
                title="Проект «Тендеры» в ошибке",
                status=502,
                detail="Не удалось перезапустить проект «Тендеры»; попробуйте ещё раз позже",
            )

    # ------------------------------------------------------------------- chat
    async def _existing_session(self, *, tender: dict[str, Any], target_id: str) -> str:
        imported = tender.get("imported_chat_ids")
        ids = [str(x) for x in imported] if isinstance(imported, list) else []
        for sid in ids:
            row = await self._session.get(AgentSessionRow, sid)
            if row is not None and row.project_id == target_id:
                return sid
        return ""

    async def _create_session_with_retry(
        self, *, target: ProjectRow, body: dict[str, Any], principal: Any, employee: Any
    ) -> str:
        title = f"Тендер: {str(body.get('title') or '')[:120]}".strip()
        for attempt in (1, 2):
            try:
                chat = await AgentSessionService(self._session).create_session(
                    project_id=target.id,
                    principal=principal,
                    employee=employee,
                    title=title,
                )
                return str(chat.get("id") or chat.get("session_id") or "")
            except AppError as exc:
                if attempt == 1 and exc.code in {"POD_NOT_RUNNING", "PROJECT_PAUSED"}:
                    await self._reload_once(target=target, principal=principal, employee=employee)
                    await self._wait_for_runtime(target.id)
                    continue
                raise
        raise AppError(code="INTERNAL", title="Не удалось создать сессию", status=500)

    async def _wait_for_runtime(self, project_id: str, *, attempts: int = 20) -> None:
        """Wait until the restarted sandbox is observed running.

        `reload_project` only asks for desired RUNNING; the sandbox takes a few
        seconds. Without this the first chat creation races the pod and fails
        even though the project was just restarted.
        """
        from prodavan.application.pod_service.query import PodQuery

        for _ in range(max(1, attempts)):
            try:
                runtime = await PodQuery(self._session).runtime_view(project_id)
            except Exception:
                runtime = None
            if runtime is not None and str(runtime.get("observed_state") or "") == "running":
                return
            await asyncio.sleep(2.0)
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Проект «Тендеры» запускается",
            status=409,
            detail="Под проекта ещё не готов; повторите через несколько секунд",
        )

    async def _attach(
        self,
        *,
        cabinet_id: str,
        project_id: str,
        tender_id: str,
        session_id: str,
        principal: Any,
        employee: Any,
    ) -> dict[str, Any]:
        from prodavan.application.modules.module_action_executor import ModuleActionExecutor

        executor = ModuleActionExecutor(self._session)
        return await executor._tender_attach(
            cabinet_id=cabinet_id,
            module_id=MODULE_ID,
            params={},
            row_id=tender_id,
            principal=principal,
            employee=employee,
            project_id=project_id,
            session_id=session_id,
        )

    async def _mark_taken(
        self,
        *,
        io: ModuleRowIO,
        tender_id: str,
        body: dict[str, Any],
        session_id: str,
        employee: Any,
    ) -> None:
        patch = dict(body)
        imported = patch.get("imported_chat_ids")
        chats = [str(x) for x in imported] if isinstance(imported, list) else []
        if session_id not in chats:
            chats.append(session_id)
        patch["imported_chat_ids"] = chats
        patch["work_status"] = "in_progress"
        patch["status"] = "active"
        patch["taken_at"] = datetime.now(UTC).isoformat()
        if employee is not None:
            patch["taken_by"] = str(getattr(employee, "id", "") or "")
        await io.update(TENDERS_TABLE, tender_id, patch)

    async def _kickoff(
        self,
        *,
        project_id: str,
        session_id: str,
        body: dict[str, Any],
        principal: Any,
        employee: Any,
    ) -> str | None:
        text = _kickoff_text(body)
        try:
            turn = await AgentSessionService(self._session).start_detached_turn(
                project_id=project_id,
                session_id=session_id,
                text=text,
                attachment_refs=None,
                principal=principal,
                employee=employee,
                model=None,
                turn_id=uuid4().hex,
            )
            return str(getattr(turn, "turn_id", "")) or None
        except Exception:
            _log.exception("tender kickoff failed tender=%s session=%s", body.get("source_ref"), session_id)
            return None


def _kickoff_text(body: dict[str, Any]) -> str:
    lines = body.get("lines_json")
    positions = [x for x in lines if isinstance(x, dict)] if isinstance(lines, list) else []
    parts = [
        "Тендер взял в работу. Данные ниже — из таблицы тендеров B2B-Center.",
        f"Название: {body.get('title') or '—'}",
        f"Заказчик: {body.get('customer') or '—'}",
        f"Срок подачи: {body.get('deadline_at') or '—'}",
        f"Номер процедуры: {body.get('number') or '—'}",
        f"Ссылка: {body.get('source_url') or '—'}",
        "",
        f"Позиции заказчика ({len(positions)}):",
    ]
    for i, line in enumerate(positions[:100], start=1):
        qty = line.get("qty")
        unit = line.get("unit") or ""
        pn = line.get("part_number") or ""
        title = str(line.get("title") or "—")
        qty_text = f" — {qty} {unit}".rstrip() if qty else ""
        pn_text = f" (P/N: {pn})" if pn else ""
        parts.append(f"{i}. {title}{qty_text}{pn_text}")
    parts.extend(
        [
            "",
            "Задача:",
            "1) Проверь релевантность тендера нашему профилю; при нерелевантности поставь статус invalid и объясни.",
            "2) Для каждой позиции найди товар в каталогах/интернет-магазинах и зафиксируй найденные варианты.",
            "3) Выбери лучший товар по цене/наличию/совместимости; аналоги помечай отдельно.",
            "4) Не выдумывай цены и артикулы — используй только найденные данные.",
        ]
    )
    return "\n".join(parts)[:12000]
