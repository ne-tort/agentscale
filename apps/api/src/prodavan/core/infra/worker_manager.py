"""WorkerManager — Celery app facade + LifespanResource (C-JOBS)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names
from prodavan.core.lifespan.resource import LifespanResource

logger = logging.getLogger(__name__)

_manager: WorkerManager | None = None
# Module-level Celery app for ``celery -A prodavan.core.infra.worker_manager.celery_app``
celery_app: Any = None


def get_worker_manager() -> WorkerManager | None:
    return _manager


def set_worker_manager(manager: WorkerManager | None) -> None:
    global _manager
    _manager = manager


def get_celery_app() -> Any:
    if celery_app is None:
        raise RuntimeError("Celery app is not configured (WorkerManager.startup)")
    return celery_app


def _celery_broker_from_settings() -> str | None:
    from prodavan.config.settings import settings

    return (settings.celery_broker_url or settings.redis_url or "").strip() or None


def worker_manager_from_settings() -> WorkerManager:
    """Build WorkerManager from env (shared by API lifespan and Celery CLI)."""
    from prodavan.config.settings import settings

    broker = _celery_broker_from_settings()
    return WorkerManager(
        enabled=settings.celery_enabled,
        broker_url=broker,
        result_backend=(settings.celery_result_backend or "").strip() or broker,
        trigger_interval_sec=settings.trigger_worker_interval_sec,
        schedule_trigger_drain=settings.trigger_worker_enabled,
        schedule_idle_pause=settings.idle_pause_worker_enabled,
        task_always_eager=settings.celery_task_always_eager,
    )


def bootstrap_celery_app_from_settings() -> Any:
    """Ensure ``celery_app`` exists without FastAPI lifespan (worker CLI / -B).

    When ``CELERY_ENABLED`` + broker are set, builds a full app and registers the
    WorkerManager singleton so ``enqueue_*`` helpers work inside tasks.
    Otherwise installs a minimal inert Celery instance so the ``-A`` import path
    never resolves to ``None``.
    """
    global celery_app
    from celery import Celery

    from prodavan.config.settings import settings

    if celery_app is not None and get_worker_manager() is not None and get_worker_manager().enabled:
        return celery_app

    broker = _celery_broker_from_settings()
    if not settings.celery_enabled or not broker:
        if celery_app is None:
            celery_app = Celery("prodavan")
            logger.info("worker: CLI bootstrap inert (CELERY_ENABLED=false or no broker)")
        return celery_app

    mgr = worker_manager_from_settings()
    mgr._app = mgr._build_app()
    set_worker_manager(mgr)
    celery_app = mgr._app
    logger.info(
        "worker: CLI bootstrap broker=%s beat_drain=%s beat_idle=%s",
        broker,
        mgr._schedule_trigger_drain,
        mgr._schedule_idle_pause,
    )
    return celery_app


class WorkerManager(LifespanResource):
    """Build/configure Celery; does not run the worker process inside API.

    Separate process::

        celery -A prodavan.core.infra.worker_manager.celery_app worker -l info -B

    When ``enabled`` and broker is set, in-process TriggerWorkerResource should skip
    its asyncio loop (see ``uses_celery_executor``).
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        broker_url: str | None = None,
        result_backend: str | None = None,
        trigger_interval_sec: float = 5.0,
        schedule_trigger_drain: bool = False,
        schedule_idle_pause: bool = False,
        task_always_eager: bool = False,
    ) -> None:
        self._enabled = enabled
        self._broker_url = (broker_url or "").strip() or None
        self._result_backend = (result_backend or "").strip() or self._broker_url
        self._trigger_interval_sec = max(1.0, float(trigger_interval_sec))
        self._schedule_trigger_drain = schedule_trigger_drain
        self._schedule_idle_pause = schedule_idle_pause
        self._task_always_eager = task_always_eager
        self._app: Any = None

    @property
    def name(self) -> str:
        return "worker"

    @property
    def enabled(self) -> bool:
        return bool(self._enabled and self._broker_url and self._app is not None)

    @property
    def uses_celery_executor(self) -> bool:
        """True when background jobs should use Celery instead of in-process loop."""
        return self.enabled and not self._task_always_eager

    def send_task(self, name: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        if not self.enabled:
            raise RuntimeError("Celery worker manager is not enabled")
        task = self._app.tasks.get(name)
        if task is None:
            raise KeyError(f"unknown celery task: {name}")
        args = args or []
        kwargs = kwargs or {}
        if self._task_always_eager:
            return task.apply(args=args, kwargs=kwargs)
        return task.apply_async(args=args, kwargs=kwargs)

    def _build_app(self) -> Any:
        from celery import Celery
        from celery.schedules import schedule

        from prodavan.core.jobs import tasks as job_tasks

        backend = self._result_backend
        if backend and backend.startswith("memory://"):
            backend = "cache+memory://"
        app = Celery("prodavan", broker=self._broker_url, backend=backend)
        app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            timezone="UTC",
            enable_utc=True,
            task_track_started=True,
            task_always_eager=self._task_always_eager,
            task_eager_propagates=True,
            broker_connection_retry_on_startup=True,
        )
        beat: dict[str, dict[str, Any]] = {}
        interval = schedule(run_every=self._trigger_interval_sec)
        if self._schedule_trigger_drain:
            beat["prodavan-trigger-drain"] = {
                "task": job_names.TRIGGER_DRAIN,
                "schedule": interval,
            }
        if self._schedule_idle_pause:
            beat["prodavan-idle-pause-sweep"] = {
                "task": job_names.IDLE_PAUSE_SWEEP,
                "schedule": interval,
            }
        app.conf.beat_schedule = beat
        job_tasks.register_tasks(app)
        return app

    async def startup(self) -> None:
        global celery_app
        set_worker_manager(self)
        if not self._enabled:
            logger.info("worker: Celery disabled (CELERY_ENABLED=false)")
            return
        if not self._broker_url:
            logger.warning("worker: CELERY_ENABLED but no broker URL (set REDIS_URL or CELERY_BROKER_URL)")
            return
        self._app = self._build_app()
        celery_app = self._app
        logger.info(
            "worker: Celery configured broker=%s eager=%s beat_drain=%s beat_idle=%s",
            self._broker_url,
            self._task_always_eager,
            self._schedule_trigger_drain,
            self._schedule_idle_pause,
        )

    async def shutdown(self) -> None:
        global celery_app
        self._app = None
        celery_app = None
        if get_worker_manager() is self:
            set_worker_manager(None)

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        if not self._broker_url:
            return False
        return self._app is not None


# Import-time bootstrap so ``celery -A …celery_app`` works without API lifespan.
celery_app = bootstrap_celery_app_from_settings()
