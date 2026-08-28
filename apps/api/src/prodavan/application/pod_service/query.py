"""PodQuery — read facade for project pods."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.factory import build_pod_metrics, build_pod_runtime
from prodavan.config.settings import settings
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
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            return None
        pod = self._public(row)
        summary = {
            "pod_id": pod["id"],
            "status": pod["status"],
            "desired_state": pod["desired_state"],
            "runtime_ref": pod["runtime_ref"],
            "last_error": pod["last_error"],
            "hydrate_generation": pod["hydrate_generation"],
        }
        mode = (settings.pod_runtime_mode or "stub").strip().lower()
        if mode == "k8s" and row.runtime_ref:
            try:
                runtime = build_pod_runtime()
                k8s = await runtime.get_status(runtime_ref=row.runtime_ref)
                summary["phase"] = k8s.get("phase")
                summary["restarts"] = k8s.get("restarts")
                summary["ready"] = k8s.get("ready")
                summary["runtime_uid"] = k8s.get("uid")
                metrics_port = build_pod_metrics()
                if metrics_port is not None:
                    metrics = await metrics_port.get_pod_metrics(runtime_ref=row.runtime_ref)
                    if metrics:
                        summary["metrics"] = metrics
            except Exception:
                summary["phase"] = "Unknown"
        return summary

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
