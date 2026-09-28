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
from prodavan.domain.pods import POD_TERMINAL_STATUSES, PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.agent import (
    AgentEventRow,
    AgentSessionRow,
    EmployeeChatPinRow,
    EmployeeProjectSelectionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

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


def _split_visible(
    rows: list[AgentSessionRow],
    *,
    pinned_ids: set[str],
    draft_ids: set[str],
    now: datetime,
) -> tuple[list[AgentSessionRow], list[AgentSessionRow]]:
    """Split project sessions into visible chats and abandoned empty shells.

    Visibility: empty shells (no messages, no draft) are hidden; abandoned ones
    past the grace window are returned for GC. Pinned shells mirror the pinned
    list rule — hidden, but never GC'd from the sidebar.
    """
    visible: list[AgentSessionRow] = []
    to_gc: list[AgentSessionRow] = []
    for r in rows:
        has_draft = r.id in draft_ids
        has_messages = r.last_message_at is not None
        if not has_messages and not has_draft:
            # Hide empty shells; only GC abandoned ones past the grace window.
            if r.id not in pinned_ids and not _is_young_empty_shell(r, now=now):
                to_gc.append(r)
            continue
        visible.append(r)
    return visible, to_gc


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
        if selected_project_id and selected_project_id in projects:
            proj = projects[selected_project_id]
            # New chat only when project is active and container is running —
            # not draft/paused/error/launching/failed.
            if proj.status == "active":
                runtime = await PodQuery(self._session).runtime_view(proj.id)
                observed_state = runtime.get("observed_state") if runtime else None
                new_chat_enabled = observed_state == "running"

        # Tree branches: one entry per alive project in the cabinet — the
        # sidebar is NOT bound to the selected project. Soft-deleted projects
        # are skipped (same rule as cabinet project listing); everything else
        # (draft/active/paused/error/completed) gets a branch.
        tree_projects = sorted(
            (p for p in projects.values() if p.status != ProjectStatus.DELETED),
            key=lambda p: ((p.name or "").lower(), p.id),
        )
        tree_id_set = {p.id for p in tree_projects}
        tree_ids = [p.id for p in tree_projects]

        # Per-branch new_chat_enabled without N+1 runtime_view calls: the
        # selected project keeps the exact runtime check above; every other
        # active project is gated from live pod rows fetched in ONE query.
        active_tree_ids = [p.id for p in tree_projects if p.status == ProjectStatus.ACTIVE]
        pod_rows: dict[str, ProjectPodRow] = {}
        if active_tree_ids:
            pods_q = await self._session.execute(
                select(ProjectPodRow).where(
                    ProjectPodRow.project_id.in_(active_tree_ids),
                    ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
                )
            )
            for pod in pods_q.scalars().all():
                if pod.project_id is not None:
                    pod_rows[pod.project_id] = pod

        # Sessions of every cabinet project in ONE query (visible statuses
        # only). Also covers a soft-deleted selected project so the legacy
        # project_chats field keeps its old semantics.
        query_ids = list(tree_ids)
        if selected_project_id and selected_project_id in projects and selected_project_id not in tree_id_set:
            query_ids.append(selected_project_id)
        sessions_by_project: dict[str, list[AgentSessionRow]] = {}
        if query_ids:
            sess_q = await self._session.execute(
                select(AgentSessionRow)
                .where(AgentSessionRow.project_id.in_(query_ids))
                .where(AgentSessionRow.status.in_(["active", "suspended", "closed"]))
            )
            for row in sess_q.scalars().all():
                sessions_by_project.setdefault(row.project_id, []).append(row)

        empty_to_gc: list[AgentSessionRow] = []
        visible_by_project: dict[str, list[AgentSessionRow]] = {}
        projects_payload: list[dict] = []
        for proj in tree_projects:
            rows = sessions_by_project.get(proj.id, [])
            visible, to_gc = _split_visible(
                rows, pinned_ids=pinned_ids, draft_ids=draft_ids, now=now
            )
            empty_to_gc.extend(to_gc)
            visible_by_project[proj.id] = visible
            # Branch order: pinned first (recency inside the pinned tier),
            # then the rest by recency.
            visible.sort(key=lambda r: (r.id in pinned_ids, _sort_key(r)), reverse=True)
            if proj.id == selected_project_id:
                branch_new_chat = new_chat_enabled
            elif proj.status == ProjectStatus.ACTIVE:
                pod = pod_rows.get(proj.id)
                branch_new_chat = (
                    pod is not None
                    and pod.status == PodStatus.RUNNING
                    and pod.desired_state == PodDesiredState.RUNNING.value
                )
            else:
                branch_new_chat = False
            projects_payload.append(
                {
                    "project_id": proj.id,
                    "project_name": proj.name,
                    "status": proj.status,
                    "new_chat_enabled": branch_new_chat,
                    "chats": [
                        _chat_item(
                            r,
                            project_name=proj.name,
                            pinned=r.id in pinned_ids,
                            has_draft=r.id in draft_ids,
                        )
                        for r in visible
                    ],
                }
            )

        # Legacy field (backward compat): selected project's chats, pinned
        # excluded, recency order — same shape as before the tree.
        if selected_project_id and selected_project_id in projects:
            if selected_project_id in tree_id_set:
                sel_visible = visible_by_project.get(selected_project_id, [])
            else:
                # Soft-deleted selected project: no branch, but the legacy
                # list keeps listing its chats.
                sel_visible, sel_gc = _split_visible(
                    sessions_by_project.get(selected_project_id, []),
                    pinned_ids=pinned_ids,
                    draft_ids=draft_ids,
                    now=now,
                )
                empty_to_gc.extend(sel_gc)
            legacy_rows = [r for r in sel_visible if r.id not in pinned_ids]
            legacy_rows.sort(key=_sort_key, reverse=True)
            project_chats = [
                _chat_item(
                    r,
                    project_name=projects[selected_project_id].name,
                    pinned=False,
                    has_draft=r.id in draft_ids,
                )
                for r in legacy_rows
            ]

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
            "projects": projects_payload,
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
