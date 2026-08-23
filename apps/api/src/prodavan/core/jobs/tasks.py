"""Celery tasks — background jobs (C-JOBS).

Workers run out-of-process: ``celery -A prodavan.core.infra.worker_manager.celery_app worker -B``
"""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.async_runner import run_async

logger = logging.getLogger(__name__)


def _get_app():
    from prodavan.core.infra.worker_manager import get_celery_app

    return get_celery_app()


def register_tasks(app) -> None:
    """Bind tasks to a Celery app (called from WorkerManager)."""

    @app.task(name=job_names.TRIGGER_DRAIN, bind=False)
    def trigger_drain() -> dict[str, Any]:
        from prodavan.application.agent.trigger_worker import drain_once

        logger.info("celery task %s", job_names.TRIGGER_DRAIN)
        return run_async(drain_once())

    @app.task(name=job_names.IDLE_PAUSE_SWEEP, bind=False)
    def idle_pause_sweep() -> dict[str, Any]:
        from prodavan.application.projects.idle_pause_service import IdlePauseService
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            factory = get_session_factory()
            async with factory() as session:
                return await IdlePauseService(session).sweep_all()

        logger.info("celery task %s", job_names.IDLE_PAUSE_SWEEP)
        return run_async(_run())

    @app.task(name=job_names.REMATERIALIZE_PROJECT, bind=False)
    def rematerialize_project(project_id: str) -> dict[str, Any]:
        from prodavan.application.projects.materialize import ProjectMaterializeService
        from prodavan.infrastructure.persistence.database import get_session_factory
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        async def _run() -> dict[str, Any]:
            factory = get_session_factory()
            async with factory() as session:
                row = await session.get(ProjectRow, project_id)
                if row is None:
                    return {"ok": False, "reason": "not_found", "project_id": project_id}
                result = await ProjectMaterializeService().materialize_project(
                    session=session,
                    project_id=row.id,
                    cabinet_id=row.cabinet_id,
                    project_name=row.name,
                )
                await session.commit()
                return {
                    "ok": True,
                    "project_id": project_id,
                    "workspace_root": result.workspace_root,
                    "status": result.status,
                }

        logger.info("celery task %s project_id=%s", job_names.REMATERIALIZE_PROJECT, project_id)
        return run_async(_run())
