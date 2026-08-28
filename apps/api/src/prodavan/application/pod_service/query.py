"""PodQuery — read facade for project pods."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.pods import POD_TERMINAL_STATUSES
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow


class PodQuery:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_project(self, project_id: str) -> dict | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        row = q.scalar_one_or_none()
        return self._public(row) if row else None

    async def runtime_summary(self, project_id: str) -> dict | None:
        pod = await self.get_for_project(project_id)
        if pod is None:
            return None
        return {
            "pod_id": pod["id"],
            "status": pod["status"],
            "desired_state": pod["desired_state"],
            "runtime_ref": pod["runtime_ref"],
            "last_error": pod["last_error"],
        }

    async def list_orphans(self) -> list[dict]:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id.is_(None),
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        return [self._public(row) for row in q.scalars().all()]

    @staticmethod
    def _public(row: ProjectPodRow) -> dict:
        return {
            "id": row.id,
            "project_id": row.project_id,
            "workspace_key": row.workspace_key,
            "status": row.status,
            "desired_state": row.desired_state,
            "runtime_ref": row.runtime_ref,
            "last_error": row.last_error,
            "hydrate_generation": row.hydrate_generation,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

