"""Celery tasks — background jobs (C-JOBS).

Workers run out-of-process: ``celery -A prodavan.core.infra.worker_manager.celery_app worker -B``
"""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.async_runner import run_async
from prodavan.core.jobs.locks import run_with_job_lock

logger = logging.getLogger(__name__)


def _get_app():
    from prodavan.core.infra.worker_manager import get_celery_app

    return get_celery_app()


def register_tasks(app) -> None:
    """Bind tasks to a Celery app (called from WorkerManager)."""

    @app.task(name=job_names.TRIGGER_DRAIN, bind=False)
    def trigger_drain() -> dict[str, Any]:
        from prodavan.application.agent.trigger_worker import drain_once
        from prodavan.config.settings import settings

        async def _run() -> dict[str, Any]:
            return await run_with_job_lock(
                "trigger_drain",
                ttl_sec=max(30, int(float(settings.trigger_worker_interval_sec or 5) * 2)),
                fn=drain_once,
            )

        logger.info("celery task %s", job_names.TRIGGER_DRAIN)
        return run_async(_run())

    @app.task(name=job_names.DISPATCH_TRIGGER, bind=False)
    def dispatch_trigger(trigger_id: str) -> dict[str, Any]:
        from prodavan.application.agent.trigger_dispatcher import AgentTriggerDispatcher
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            factory = get_session_factory()
            async with factory() as session:
                return await AgentTriggerDispatcher(session).dispatch_trigger_id(
                    trigger_id=trigger_id
                )

        logger.info("celery task %s trigger_id=%s", job_names.DISPATCH_TRIGGER, trigger_id)
        return run_async(_run())

    @app.task(name=job_names.IDLE_PAUSE_SWEEP, bind=False)
    def idle_pause_sweep() -> dict[str, Any]:
        from prodavan.application.project_service import ProjectIdlePauseService
        from prodavan.config.settings import settings
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _sweep() -> dict[str, Any]:
            factory = get_session_factory()
            async with factory() as session:
                return await ProjectIdlePauseService(session).sweep_all()

        async def _run() -> dict[str, Any]:
            return await run_with_job_lock(
                "idle_pause_sweep",
                ttl_sec=max(30, int(float(settings.trigger_worker_interval_sec or 5) * 2)),
                fn=_sweep,
            )

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

    @app.task(name=job_names.WIPE_CABINET_PACKAGES, bind=False)
    def wipe_cabinet_packages(cabinet_id: str) -> dict[str, Any]:
        # MCP package blobs removed from cabinet entity; keep task name for queue compat.
        logger.info("celery task %s cabinet_id=%s (noop)", job_names.WIPE_CABINET_PACKAGES, cabinet_id)
        return {"ok": True, "deleted": 0, "remaining": 0, "cabinet_id": cabinet_id, "noop": True}

    @app.task(name=job_names.WIPE_PROJECT_TREE, bind=False)
    def wipe_project_tree(workspace_key: str) -> dict[str, Any]:
        from prodavan.application.projects.project_wipe import wipe_project_tree as wipe_fn

        logger.info("celery task %s workspace_key=%s", job_names.WIPE_PROJECT_TREE, workspace_key)
        return wipe_fn(workspace_key)

    @app.task(name=job_names.GC_ORPHAN_CABINET_SCHEMAS, bind=False)
    def gc_orphan_cabinet_schemas(dry_run: bool = False, limit: int = 50) -> dict[str, Any]:
        from prodavan.application.cabinets.schema_gc import gc_orphan_cabinet_schemas as gc_fn
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _gc() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await gc_fn(session, dry_run=bool(dry_run), limit=int(limit))

            return await run_with_job_lock(
                "gc_orphan_cabinet_schemas",
                ttl_sec=120,
                fn=_gc,
            )

        logger.info(
            "celery task %s dry_run=%s limit=%s",
            job_names.GC_ORPHAN_CABINET_SCHEMAS,
            dry_run,
            limit,
        )
        return run_async(_run())

    @app.task(name=job_names.GC_ORPHAN_BLOBS, bind=False)
    def gc_orphan_blobs(
        dry_run: bool = False,
        limit: int = 50,
        scan_limit: int = 500,
    ) -> dict[str, Any]:
        from prodavan.application.infra.blob_gc import gc_orphan_blobs as gc_fn
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _gc() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await gc_fn(
                        session,
                        dry_run=bool(dry_run),
                        limit=int(limit),
                        scan_limit=int(scan_limit),
                    )

            return await run_with_job_lock(
                "gc_orphan_blobs",
                ttl_sec=300,
                fn=_gc,
            )

        logger.info(
            "celery task %s dry_run=%s limit=%s scan_limit=%s",
            job_names.GC_ORPHAN_BLOBS,
            dry_run,
            limit,
            scan_limit,
        )
        return run_async(_run())

    @app.task(name=job_names.APPLY_AUTH_USER_REGISTERED, bind=False)
    def apply_auth_user_registered(payload: dict[str, Any]) -> dict[str, Any]:
        from prodavan.application.identity.auth_bind import apply_auth_user_registered_payload
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            factory = get_session_factory()
            async with factory() as session:
                return await apply_auth_user_registered_payload(session, payload or {})

        logger.info(
            "celery task %s client_ref=%s",
            job_names.APPLY_AUTH_USER_REGISTERED,
            (payload or {}).get("client_ref"),
        )
        return run_async(_run())

    @app.task(name=job_names.CASCADE_COMPANY_DELETED, bind=False)
    def cascade_company_deleted(company_id: str, actor_sub: str = "system") -> dict[str, Any]:
        from prodavan.application.companies.cascade import cascade_company_deleted as cascade_fn

        logger.info(
            "celery task %s company_id=%s",
            job_names.CASCADE_COMPANY_DELETED,
            company_id,
        )
        return run_async(cascade_fn(company_id, actor_sub=actor_sub or "system"))
