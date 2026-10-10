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


def _register_worker_k8s_bootstrap(app) -> None:
    """Start K8sManager + OpenSearch + FileStore in Celery worker processes."""
    from celery.signals import worker_process_init

    @worker_process_init.connect(weak=False)
    def _bootstrap_worker_infra(**_kwargs) -> None:
        from prodavan.config.settings import settings
        from prodavan.core.infra.k8s_manager import get_k8s_manager, k8s_manager_from_settings
        from prodavan.core.infra.opensearch_manager import OpenSearchManager, get_opensearch_manager
        from prodavan.infrastructure.files.manager import FileStoreManager, get_file_store_optional

        async def _start() -> None:
            mode = (settings.pod_runtime_mode or "stub").strip().lower()
            if mode == "k8s" and get_k8s_manager() is None:
                mgr = k8s_manager_from_settings()
                await mgr.startup()
                logger.info(
                    "worker: K8sManager started namespace=%s", settings.pod_sandbox_namespace
                )

            if mode == "sandbox":
                # pod_reconcile / idle_pause / rematerialize run in this
                # worker and drive the agent-sandbox adapter — without the
                # SandboxClientResource every reconcile pass fails with
                # SandboxRuntimeUnavailableError.
                from prodavan.core.infra.sandbox_client import (
                    get_sandbox_client_manager,
                    sandbox_client_resource_from_settings,
                )

                if get_sandbox_client_manager() is None:
                    sbx = sandbox_client_resource_from_settings()
                    await sbx.startup()
                    logger.info(
                        "worker: SandboxClientResource started router=%s namespace=%s",
                        settings.pod_sandbox_router_url,
                        settings.pod_sandbox_namespace,
                    )

            if get_opensearch_manager() is None:
                os_mgr = OpenSearchManager(
                    url=settings.opensearch_url,
                    enabled=settings.opensearch_enabled
                    or bool((settings.opensearch_url or "").strip()),
                    required=False,
                    username=settings.opensearch_username,
                    password=settings.opensearch_password,
                )
                await os_mgr.startup()
                logger.info("worker: OpenSearchManager started enabled=%s", os_mgr.enabled)

            if get_file_store_optional() is None or get_file_store_optional()._primary is None:
                backend = (settings.object_store_backend or "local").strip().lower()
                if backend not in ("local", "s3"):
                    backend = "local"
                fs = FileStoreManager(
                    backend=backend,  # type: ignore[arg-type]
                    storage_root=settings.storage_root,
                    s3_endpoint_url=settings.s3_endpoint_url,
                    s3_access_key=settings.s3_access_key,
                    s3_secret_key=settings.s3_secret_key,
                    s3_bucket=settings.s3_bucket,
                    s3_region=settings.s3_region,
                    mirror_local=settings.object_store_mirror_local,
                    required=False,
                )
                await fs.startup()
                logger.info("worker: FileStoreManager started backend=%s", backend)

        run_async(_start())


def _get_app():
    from prodavan.core.infra.worker_manager import get_celery_app

    return get_celery_app()


def register_tasks(app) -> None:
    """Bind tasks to a Celery app (called from WorkerManager)."""
    _register_worker_k8s_bootstrap(app)

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
        from prodavan.application.project_service.command import ProjectCommand
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            pid = (project_id or "").strip()
            lock_job = f"rematerialize:{pid}"

            async def _work() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await ProjectCommand(session).rematerialize_background(project_id=pid)

            return await run_with_job_lock(lock_job, ttl_sec=300, fn=_work)

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

    @app.task(name=job_names.POD_RECONCILE, bind=False)
    def pod_reconcile() -> dict[str, Any]:
        from prodavan.application.pod_service import PodReconcileService
        from prodavan.config.settings import settings
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _reconcile() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await PodReconcileService(session).run()

            return await run_with_job_lock(
                "pod_reconcile",
                ttl_sec=max(60, int(float(settings.trigger_worker_interval_sec or 5) * 4)),
                fn=_reconcile,
            )

        logger.info("celery task %s", job_names.POD_RECONCILE)
        return run_async(_run())

    @app.task(
        name=job_names.INDEX_EQUIPMENT_CATALOG,
        bind=False,
        # Late ack: a worker killed mid-index (deploy/SIGKILL) re-queues the
        # job instead of dropping it — the catalog row would otherwise stay
        # «В процессе» forever.
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def index_equipment_catalog(
        instance_id: str,
        row_id: str,
        company_id: str,
        cabinet_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.equipment_catalog_opensearch import (
            run_index_equipment_catalog,
        )
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            rid = (row_id or "").strip()

            async def _work() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await run_index_equipment_catalog(
                        session,
                        instance_id=(instance_id or "").strip(),
                        row_id=rid,
                        company_id=(company_id or "").strip(),
                        cabinet_id=cabinet_id,
                        project_id=project_id,
                    )

            return await run_with_job_lock(f"index_os:{rid}", ttl_sec=3600, fn=_work)

        logger.info(
            "celery task %s row_id=%s",
            job_names.INDEX_EQUIPMENT_CATALOG,
            row_id,
        )
        return run_async(_run())

    @app.task(name=job_names.AGENT_RUN_STALL_SWEEP, bind=False)
    def agent_run_stall_sweep() -> dict[str, Any]:
        from prodavan.application.agent.run_stall import AgentRunStallService
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _sweep() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await AgentRunStallService(session).sweep_all()

            return await run_with_job_lock(
                "agent_run_stall_sweep",
                ttl_sec=300,
                fn=_sweep,
            )

        logger.info("celery task %s", job_names.AGENT_RUN_STALL_SWEEP)
        return run_async(_run())

    @app.task(name=job_names.SWEEP_EQUIPMENT_CATALOG_REINDEX, bind=False)
    def sweep_equipment_catalog_reindex() -> dict[str, Any]:
        from prodavan.application.modules.equipment_catalog_reindex_sweep import (
            sweep_due_equipment_catalogs,
        )
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _sweep() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await sweep_due_equipment_catalogs(session)

            return await run_with_job_lock(
                "sweep_equipment_catalog_reindex",
                ttl_sec=600,
                fn=_sweep,
            )

        logger.info("celery task %s", job_names.SWEEP_EQUIPMENT_CATALOG_REINDEX)
        return run_async(_run())

    @app.task(name=job_names.SWEEP_READY_BUILDS, bind=False)
    def sweep_ready_builds() -> dict[str, Any]:
        """WAVE11: автообновление каталога «Готовые сборки».

        Цены меняются в каталоге поставщика, а не в наших строках, поэтому
        одних триггеров на запись мало — sweep периодически резолвит ключи
        пула (партномер/алиасы/хэш) в свежие лучшие цены и пересчитывает
        составы dynamic-слотов и итоги сборок.
        """
        from prodavan.application.modules.equipment_ready_builds_sweep import (
            sweep_ready_builds as _sweep_catalog,
        )
        from prodavan.infrastructure.persistence.database import get_session_factory

        async def _run() -> dict[str, Any]:
            async def _work() -> dict[str, Any]:
                factory = get_session_factory()
                async with factory() as session:
                    return await _sweep_catalog(session)

            return await run_with_job_lock(
                "sweep_ready_builds",
                ttl_sec=900,
                fn=_work,
            )

        logger.info("celery task %s", job_names.SWEEP_READY_BUILDS)
        return run_async(_run())
