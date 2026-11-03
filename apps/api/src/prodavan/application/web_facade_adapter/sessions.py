"""Server-side chat session resolution for the SSE transport.

A ``session_id`` is never taken from JWT claims or from an event payload. The
transport resolves it from the route and proves it belongs to the requested
project before the facade context is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.project_service import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


@dataclass(frozen=True, slots=True)
class ResolvedChatSession:
    """Project/session pair proven against the tenant and project scope."""

    project_id: str
    session_id: str
    cabinet_id: str


class ChatSessionResolver(Protocol):
    async def resolve(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str,
        session_id: str,
    ) -> ResolvedChatSession: ...


class SqlChatSessionResolver:
    """Resolve project access and session ownership in one server-side step."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        project_id: str,
        session_id: str,
    ) -> ResolvedChatSession:
        if not project_id.strip():
            raise AppError(
                code="PROJECT_CONTEXT_MISSING",
                title="Project context required",
                status=400,
                detail="a route project is required for a chat stream",
            )
        if not session_id.strip():
            raise AppError(
                code="SESSION_CONTEXT_MISSING",
                title="Session context required",
                status=400,
                detail="a route session is required for a chat stream",
            )
        if employee is None:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="an active employee is required for a chat stream",
            )

        # Tenant/cabinet/visibility check happens before the session row is
        # even read, so a foreign session id cannot be probed for existence.
        project = await ProjectAccessPolicy(self._session).require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=False,
            allow_paused=True,
        )
        row = await self._session.get(AgentSessionRow, session_id)
        if row is None or row.project_id != project_id:
            # Hidden by policy: a session in another project is indistinguishable
            # from a missing one.
            raise AppError(
                code="SESSION_NOT_FOUND",
                title="Not Found",
                status=404,
                detail="chat session not found in the requested project",
            )
        return ResolvedChatSession(
            project_id=project_id,
            session_id=row.id,
            cabinet_id=project.cabinet_id,
        )
