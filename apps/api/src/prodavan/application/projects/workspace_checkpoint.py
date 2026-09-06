"""Project workspace checkpoint — dehydrate live Pod /workspace → MinIO last-good."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.factory import build_dehydrate
from prodavan.application.pod_service.ports.dehydrate import DehydratePort, DehydrateResult
from prodavan.domain.pods import PodStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)


async def checkpoint_project_workspace(
    session: AsyncSession,
    *,
    project_id: str,
    dehydrate: DehydratePort | None = None,
    best_effort: bool = True,
) -> DehydrateResult | None:
    """Sync live Pod workspace into object store before pause/reload or after a turn.

    Returns None when there is no running pod / runtime_ref (nothing to copy).
    When ``best_effort`` is True, errors are logged and None is returned instead of raising.
    """
    project = await session.get(ProjectRow, project_id)
    if project is None:
        return None
    q = await session.execute(
        select(ProjectPodRow).where(
            ProjectPodRow.project_id == project_id,
            ProjectPodRow.status.notin_((PodStatus.TERMINATED, PodStatus.FAILED)),
        )
    )
    pod = q.scalar_one_or_none()
    if pod is None:
        return None
    runtime_ref = str(pod.runtime_ref or project.container_ref or "").strip()
    if not runtime_ref:
        return None
    workspace_key = str(pod.workspace_key or project.workspace_key or "").strip()
    if not workspace_key:
        return None

    try:
        adapter = dehydrate or build_dehydrate()
        result = await adapter.dehydrate(workspace_key=workspace_key, runtime_ref=runtime_ref)
        logger.info(
            "workspace checkpoint project_id=%s uploaded=%s deleted=%s",
            project_id,
            result.uploaded,
            result.deleted,
        )
        return result
    except Exception:
        if best_effort:
            logger.exception("workspace checkpoint failed project_id=%s", project_id)
            return None
        raise
