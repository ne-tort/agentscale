"""PodQuery — read facade for project pods."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
from prodavan.domain.pods import POD_TERMINAL_STATUSES, PodStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


def _k8s_pod_name(observed: dict, runtime_ref: str | None) -> str | None:
    if observed.get("stub"):
        return None
    ref = (runtime_ref or "").strip()
    if ref.startswith("pod-"):
        return ref
    return None


class PodQuery:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._observation = RuntimeObservationService(session)

    async def get_for_project(self, project_id: str) -> dict | None:
        row = await self._get_live_row(project_id)
        return self._public(row) if row else None

    async def runtime_summary(self, project_id: str) -> dict | None:
        row = await self._get_live_row(project_id)
        if row is None:
            return None
        return await self._build_runtime_summary(row, project_id)

    async def runtime_view(self, project_id: str) -> dict | None:
        """Runtime for UI — includes failed pod when no live pod exists."""
        row = await self._get_live_row(project_id)
        project = await self._session.get(ProjectRow, project_id)
        if row is None:
            row = await self._get_failed_row(project_id)
        if row is None:
            if project is not None and project.launch_phase == "preparing":
                return await self._observation.observe(project=project, pod=None)
            return None
        if project is not None:
            from prodavan.application.pod_service.metrics_sampler import PodMetricsSampler
            from prodavan.config.settings import settings

            if (settings.pod_runtime_mode or "stub").strip().lower() == "k8s":
                await PodMetricsSampler(self._session).sample_project(project_id)
            action = await self._observation.sync_runtime_health(project=project, pod=row)
            if action != "noop":
                await self._session.commit()
        return await self._build_runtime_summary(row, project_id)

    async def _get_live_row(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow).where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)),
            )
        )
        return q.scalar_one_or_none()

    async def _get_failed_row(self, project_id: str) -> ProjectPodRow | None:
        q = await self._session.execute(
            select(ProjectPodRow)
            .where(
                ProjectPodRow.project_id == project_id,
                ProjectPodRow.status == PodStatus.FAILED,
            )
            .order_by(ProjectPodRow.updated_at.desc())
            .limit(1)
        )
        return q.scalar_one_or_none()

    async def _build_runtime_summary(self, row: ProjectPodRow, project_id: str) -> dict:
        pod = self._public(row)
        project = await self._session.get(ProjectRow, project_id)
        observed = await self._observation.observe(project=project, pod=row)
        summary = {
            "pod_id": pod["id"],
            "status": pod["status"],
            "orchestrator_status": pod["status"],
            "desired_state": pod["desired_state"],
            "runtime_ref": pod["runtime_ref"],
            "k8s_pod_name": _k8s_pod_name(observed, pod.get("runtime_ref")),
            "last_error": observed.get("last_error") or pod["last_error"],
            "hydrate_generation": pod["hydrate_generation"],
            "pod_created_at": pod["created_at"],
            "pod_updated_at": pod["updated_at"],
            "last_started_at": pod["last_started_at"],
            **observed,
        }
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
            "last_started_at": row.last_started_at.isoformat() if row.last_started_at else None,
            "hydrate_generation": row.hydrate_generation,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
