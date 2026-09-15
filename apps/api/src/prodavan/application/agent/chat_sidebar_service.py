"""Employee chat sidebar — selection, pins, sorted chat lists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.composer_draft_service import ComposerDraftService
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.pod_service.query import PodQuery
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.agent import (
    AgentEventRow,
    AgentSessionRow,
    EmployeeChatPinRow,
    EmployeeProjectSelectionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow

# Keep brand-new empty shells (settings open / mid-send) so sidebar GC cannot
# delete them before the first user message lands — that caused 404 on send.
_EMPTY_SHELL_GC_GRACE = timedelta(minutes=30)


def _is_young_empty_shell(row: AgentSessionRow, *, now: datetime) -> bool:
    created = row.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created >= now - _EMPTY_SHELL_GC_GRACE


def _chat_item(row: AgentSessionRow, *, project_name: str, pinned: bool, has_draft: bool = False) -> dict:
    return {
        "session_id": row.id,
        "project_id": row.project_id,
        "project_name": project_name,
        "title": row.title,
        "pinned": pinned,
        "has_draft": has_draft,
        "has_messages": row.last_message_at is not None,
        "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _sort_key(row: AgentSessionRow) -> datetime:
    return row.last_message_at or row.created_at or datetime.min.replace(tzinfo=UTC)


class ChatSidebarService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cabinets = CabinetAccessService(session)

    async def get_selection(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow,
    ) -> dict:
        await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        row = await self._session.get(
            EmployeeProjectSelectionRow, {"employee_id": employee.id, "cabinet_id": cabinet_id}
        )
        return {
            "cabinet_id": cabinet_id,
            "project_id": row.project_id if row else None,
        }

    async def set_selection(
        self,
        *,
        cabinet_id: str,
        project_id: str | None,
        principal: Principal,
        employee: EmployeeRow,
    ) -> dict:
        await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        if project_id is not None:
            project = await self._session.get(ProjectRow, project_id)
            if project is None or project.cabinet_id != cabinet_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")

        row = await self._session.get(
            EmployeeProjectSelectionRow, {"employee_id": employee.id, "cabinet_id": cabinet_id}
        )
        if row is None:
            row = EmployeeProjectSelectionRow(
                employee_id=employee.id,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )
            self._session.add(row)
        else:
            row.project_id = project_id
        await self._session.commit()
        return {"cabinet_id": cabinet_id, "project_id": project_id}

    async def sidebar(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow,
    ) -> dict:
        await self._cabinets.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        sel = await self._session.get(
            EmployeeProjectSelectionRow, {"employee_id": employee.id, "cabinet_id": cabinet_id}
        )
        selected_project_id = sel.project_id if sel else None

        projects_q = await self._session.execute(
            select(ProjectRow).where(ProjectRow.cabinet_id == cabinet_id)
        )
        projects = {p.id: p for p in projects_q.scalars().all()}
        project_ids = list(projects.keys())

        pins_q = await self._session.execute(
            select(EmployeeChatPinRow).where(EmployeeChatPinRow.employee_id == employee.id)
        )
        pin_rows = list(pins_q.scalars().all())
        pinned_ids = {p.session_id for p in pin_rows}
        draft_ids = await ComposerDraftService(self._session).draft_session_ids_for_employee(
            employee_id=employee.id,
            project_ids=project_ids,
        )

        now = datetime.now(tz=UTC)
        pinned: list[dict] = []
        if pinned_ids:
            sess_q = await self._session.execute(
                select(AgentSessionRow).where(AgentSessionRow.id.in_(pinned_ids))
            )
            for row in sess_q.scalars().all():
                proj = projects.get(row.project_id)
                if proj is None:
                    # Pin for session outside this cabinet — skip.
                    continue
                has_draft = row.id in draft_ids
                has_messages = row.last_message_at is not None
                if not has_messages and not has_draft:
                    continue
                pinned.append(
                    _chat_item(
                        row,
                        project_name=proj.name,
                        pinned=True,
                        has_draft=has_draft,
                    )
                )
            pinned.sort(key=lambda c: c["last_message_at"] or c["created_at"] or "", reverse=True)

        project_chats: list[dict] = []
        new_chat_enabled = False
        observed_state: str | None = None
        empty_to_gc: list[AgentSessionRow] = []
        if selected_project_id and selected_project_id in projects:
            proj = projects[selected_project_id]
            # New chat only when project is active and container is running —
            # not draft/paused/error/launching/failed.
            if proj.status == "active":
                runtime = await PodQuery(self._session).runtime_view(proj.id)
                observed_state = runtime.get("observed_state") if runtime else None
                new_chat_enabled = observed_state == "running"
            sess_q = await self._session.execute(
                select(AgentSessionRow)
                .where(AgentSessionRow.project_id == selected_project_id)
                .where(AgentSessionRow.status.in_(["active", "suspended", "closed"]))
            )
            rows = [r for r in sess_q.scalars().all() if r.id not in pinned_ids]
            rows.sort(key=_sort_key, reverse=True)
            pname = proj.name
            visible: list[dict] = []
            for r in rows:
                has_draft = r.id in draft_ids
                has_messages = r.last_message_at is not None
                if not has_messages and not has_draft:
                    # Hide empty shells; only GC abandoned ones past the grace window.
                    if not _is_young_empty_shell(r, now=now):
                        empty_to_gc.append(r)
                    continue
                visible.append(
                    _chat_item(r, project_name=pname, pinned=False, has_draft=has_draft)
                )
            project_chats = visible

        # Drop empty shells (created then abandoned with no draft / no messages).
        for row in empty_to_gc:
            # Skip if any events somehow without last_message_at.
            ev = await self._session.execute(
                select(AgentEventRow.id).where(AgentEventRow.session_id == row.id).limit(1)
            )
            if ev.scalar_one_or_none() is not None:
                continue
            await self._session.delete(row)
        if empty_to_gc:
            await self._session.commit()

        return {
            "cabinet_id": cabinet_id,
            "selected_project_id": selected_project_id,
            "new_chat_enabled": new_chat_enabled,
            "observed_state": observed_state,
            "pinned": pinned,
            "project_chats": project_chats,
            "project_ids_in_cabinet": project_ids,
        }

    async def set_pin(
        self,
        *,
        session_id: str,
        pinned: bool,
        principal: Principal,
        employee: EmployeeRow,
    ) -> dict:
        session_row = await self._session.get(AgentSessionRow, session_id)
        if session_row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Session not found")
        project = await self._session.get(ProjectRow, session_row.project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        await self._cabinets.require_access(
            cabinet_id=project.cabinet_id, principal=principal, employee=employee, write=False
        )

        existing = await self._session.get(
            EmployeeChatPinRow, {"employee_id": employee.id, "session_id": session_id}
        )
        if pinned and existing is None:
            self._session.add(
                EmployeeChatPinRow(employee_id=employee.id, session_id=session_id)
            )
        elif not pinned and existing is not None:
            await self._session.delete(existing)
        await self._session.commit()
        return {"session_id": session_id, "pinned": pinned}
