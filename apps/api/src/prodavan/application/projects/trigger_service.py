"""Project trigger ingress queue (L07)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.domain.errors import AppError
from prodavan.domain.projects import PROJECT_TRIGGER_KINDS, ProjectStatus, TriggerStatus
from prodavan.domain.projects.types import SUBSCRIPTION_EXEMPT_TRIGGER_KINDS
from prodavan.infrastructure.persistence.models.projects import ProjectRow, ProjectTriggerRow

class ProjectTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, *, project_id: str, kind: str, payload: dict | None = None) -> dict:
        if kind not in PROJECT_TRIGGER_KINDS:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unsupported trigger kind: {kind}",
            )
        if kind not in SUBSCRIPTION_EXEMPT_TRIGGER_KINDS:
            project = await self._session.get(ProjectRow, project_id)
            if project is None or project.status == ProjectStatus.DELETED:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
            await CompanySubscriptionGate(self._session).require_active(project.company_id)
        row = ProjectTriggerRow(
            project_id=project_id,
            kind=kind,
            payload=payload or {},
            status=TriggerStatus.QUEUED,
        )
        self._session.add(row)
        await self._session.flush()
        return {
            "id": row.id,
            "project_id": row.project_id,
            "kind": row.kind,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def list_for_project(self, *, project_id: str, limit: int = 50) -> list[dict]:
        q = await self._session.execute(
            select(ProjectTriggerRow)
            .where(ProjectTriggerRow.project_id == project_id)
            .order_by(ProjectTriggerRow.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "payload": r.payload,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in q.scalars().all()
        ]

    async def list_active_project_ids_with_queued(self, *, limit: int = 50) -> list[str]:
        """Distinct active projects that have at least one queued trigger (worker drain)."""
        q = await self._session.execute(
            select(ProjectTriggerRow.project_id)
            .join(ProjectRow, ProjectRow.id == ProjectTriggerRow.project_id)
            .where(
                ProjectTriggerRow.status == TriggerStatus.QUEUED,
                ProjectRow.status == ProjectStatus.ACTIVE,
            )
            .distinct()
            .limit(limit)
        )
        return list(q.scalars().all())
