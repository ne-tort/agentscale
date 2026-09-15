"""Composer draft cache — persist chat input across navigation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import CHAT_MAX_MESSAGE_CHARS
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.composer_draft import (
    EmployeeComposerDraftRow,
    composer_draft_scope_key,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

# Skip whitespace / accidental keystrokes — not worth persisting.
COMPOSER_DRAFT_MIN_CHARS = 5


def normalize_composer_draft_text(text: str | None) -> str | None:
    """Return trimmed text if it should be cached, else None (clear)."""
    cleaned = (text or "").strip()
    if len(cleaned) < COMPOSER_DRAFT_MIN_CHARS:
        return None
    if len(cleaned) > CHAT_MAX_MESSAGE_CHARS:
        cleaned = cleaned[:CHAT_MAX_MESSAGE_CHARS]
    return cleaned


def _public(row: EmployeeComposerDraftRow | None, *, session_id: str | None = None) -> dict:
    if row is None:
        return {"text": "", "session_id": session_id, "updated_at": None, "has_draft": False}
    return {
        "text": row.text,
        "session_id": row.session_id or session_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "has_draft": True,
    }


class ComposerDraftService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)

    async def _require_employee(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ):
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        project = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        return project, employee

    async def _get_by_scope(
        self, *, employee_id: str, scope_key: str
    ) -> EmployeeComposerDraftRow | None:
        q = await self._session.execute(
            select(EmployeeComposerDraftRow).where(
                EmployeeComposerDraftRow.employee_id == employee_id,
                EmployeeComposerDraftRow.scope_key == scope_key,
            )
        )
        return q.scalar_one_or_none()

    async def get_session_draft(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_employee(project_id=project_id, principal=principal, employee=employee)
        assert employee is not None
        row = await self._session.get(AgentSessionRow, session_id)
        if row is None or row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        draft = await self._get_by_scope(
            employee_id=employee.id,
            scope_key=composer_draft_scope_key(session_id=session_id, project_id=project_id),
        )
        return _public(draft, session_id=session_id)

    async def put_session_draft(
        self,
        *,
        project_id: str,
        session_id: str,
        text: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_employee(project_id=project_id, principal=principal, employee=employee)
        assert employee is not None
        row = await self._session.get(AgentSessionRow, session_id)
        if row is None or row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent session not found")
        scope = composer_draft_scope_key(session_id=session_id, project_id=project_id)
        meaningful = normalize_composer_draft_text(text)
        existing = await self._get_by_scope(employee_id=employee.id, scope_key=scope)
        if meaningful is None:
            if existing is not None:
                await self._session.delete(existing)
                await self._session.commit()
            return _public(None, session_id=session_id)
        if existing is None:
            existing = EmployeeComposerDraftRow(
                employee_id=employee.id,
                project_id=project_id,
                session_id=session_id,
                scope_key=scope,
                text=meaningful,
            )
            self._session.add(existing)
        else:
            existing.text = meaningful
            existing.updated_at = datetime.now(tz=UTC)
        await self._session.commit()
        await self._session.refresh(existing)
        return _public(existing, session_id=session_id)

    async def clear_session_draft(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        return await self.put_session_draft(
            project_id=project_id,
            session_id=session_id,
            text="",
            principal=principal,
            employee=employee,
        )

    async def get_pending_draft(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_employee(project_id=project_id, principal=principal, employee=employee)
        assert employee is not None
        draft = await self._get_by_scope(
            employee_id=employee.id,
            scope_key=composer_draft_scope_key(session_id=None, project_id=project_id),
        )
        return _public(draft, session_id=None)

    async def put_pending_draft(
        self,
        *,
        project_id: str,
        text: str,
        principal: Principal,
        employee: EmployeeRow | None,
        create_session,
    ) -> dict:
        """Upsert pending new-chat draft; materialize a session when text is meaningful."""
        await self._require_employee(project_id=project_id, principal=principal, employee=employee)
        assert employee is not None
        pending_scope = composer_draft_scope_key(session_id=None, project_id=project_id)
        meaningful = normalize_composer_draft_text(text)
        pending = await self._get_by_scope(employee_id=employee.id, scope_key=pending_scope)

        if meaningful is None:
            if pending is not None:
                await self._session.delete(pending)
                await self._session.commit()
            return {"text": "", "session_id": None, "updated_at": None, "has_draft": False, "materialized": False}

        # Promote to real session + session draft.
        created = await create_session(
            project_id=project_id,
            principal=principal,
            employee=employee,
            model=None,
            title=None,
        )
        session_id = str(created["id"])
        # create_session commits — re-load pending in this session.
        pending = await self._get_by_scope(employee_id=employee.id, scope_key=pending_scope)
        if pending is not None:
            await self._session.delete(pending)
            await self._session.flush()

        session_scope = composer_draft_scope_key(session_id=session_id, project_id=project_id)
        draft = EmployeeComposerDraftRow(
            employee_id=employee.id,
            project_id=project_id,
            session_id=session_id,
            scope_key=session_scope,
            text=meaningful,
        )
        self._session.add(draft)
        await self._session.commit()
        await self._session.refresh(draft)
        out = _public(draft, session_id=session_id)
        out["materialized"] = True
        return out

    async def clear_pending_draft(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_employee(project_id=project_id, principal=principal, employee=employee)
        assert employee is not None
        pending_scope = composer_draft_scope_key(session_id=None, project_id=project_id)
        pending = await self._get_by_scope(employee_id=employee.id, scope_key=pending_scope)
        if pending is not None:
            await self._session.delete(pending)
            await self._session.commit()
        return {"text": "", "session_id": None, "updated_at": None, "has_draft": False, "materialized": False}

    async def draft_session_ids_for_employee(
        self, *, employee_id: str, project_ids: list[str]
    ) -> set[str]:
        if not project_ids:
            return set()
        q = await self._session.execute(
            select(EmployeeComposerDraftRow.session_id).where(
                EmployeeComposerDraftRow.employee_id == employee_id,
                EmployeeComposerDraftRow.project_id.in_(project_ids),
                EmployeeComposerDraftRow.session_id.is_not(None),
            )
        )
        return {sid for sid in q.scalars().all() if sid}

    async def clear_session_draft_silent(self, *, employee_id: str, session_id: str) -> None:
        """Best-effort clear after send (no auth re-check)."""
        scope = f"s:{session_id}"
        existing = await self._get_by_scope(employee_id=employee_id, scope_key=scope)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
