"""Typed plugin-job registration for API and standalone Celery workers.

The composition root owns the server-declared ManifestJobTasks and its
resources. This adapter owns neither: it only resolves that registry once per
worker manager and delegates registration to ManifestJobTasks.register.
No process-global registry or FastAPI application state is consulted.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from prodavan.application.plugins.jobs.tasks import ManifestJobTasks


class PluginWorkerTasksFactory(Protocol):
    """Build the real server-owned registry inside the current worker process."""

    def __call__(self) -> ManifestJobTasks:
        ...


class PluginWorkerConfigurationError(RuntimeError):
    """Safe typed startup failure; code never includes exception details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PluginWorkerRegistration:
    """Register one real manifest registry on one or more Celery applications.

    enabled is the plugin jobs decision made by the composition root. A
    disabled hook is a strict no-op and does not even call the factory. An
    enabled hook without a real registry/factory fails at worker startup. With
    no hook configured by production composition, plugin jobs remain unwired
    and the legacy worker keeps its existing behavior.

    Registration is idempotent through the original ManifestJobTasks. Its
    name-conflict and fingerprint checks remain authoritative; this class does
    not maintain a shadow task registry. API close remains with the platform
    adapter; only a standalone factory's explicit owner is closed by the worker.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        tasks: ManifestJobTasks | None = None,
        tasks_factory: PluginWorkerTasksFactory | None = None,
        defer_until_worker_init: bool = False,
    ) -> None:
        self._enabled = bool(enabled)
        self._tasks = tasks
        self._tasks_factory = tasks_factory
        self._factory_owner = (
            tasks_factory if callable(getattr(tasks_factory, "aclose", None)) else None
        )
        self._closed = False
        self._shutdown_registered = False
        self._defer_until_worker_init = bool(defer_until_worker_init)
        self._deferred_celery_app: Any = None
        self._worker_init_registered = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def wired(self) -> bool:
        """Whether a registry was supplied or has already been resolved."""
        return self._tasks is not None

    def _resolve_tasks(self) -> ManifestJobTasks:
        if self._closed:
            raise PluginWorkerConfigurationError("plugin_job_registration_closed")
        from prodavan.application.plugins.jobs.tasks import (
            JobTaskError,
            ManifestJobTasks,
        )

        factory = self._tasks_factory
        if self._tasks is not None and factory is not None:
            raise PluginWorkerConfigurationError("plugin_job_tasks_source_ambiguous")
        if self._tasks is None:
            if factory is None:
                raise PluginWorkerConfigurationError(
                    "plugin_job_tasks_registry_or_factory_required"
                )
            if not callable(factory):
                raise PluginWorkerConfigurationError("plugin_job_tasks_factory_invalid")
            try:
                self._tasks = factory()
            except JobTaskError:
                raise
            except Exception:
                raise PluginWorkerConfigurationError(
                    "plugin_job_tasks_factory_failed"
                ) from None
        if not isinstance(self._tasks, ManifestJobTasks):
            self._tasks = None
            raise PluginWorkerConfigurationError("plugin_job_tasks_registry_invalid")
        # A successfully resolved factory is consumed once. Re-registering an app
        # reuses this exact registry and never builds duplicate task wrappers.
        self._tasks_factory = None
        return self._tasks

    def registration_hook(self, tasks: ManifestJobTasks) -> None:
        """Bind the existing API registry for job_worker_registration composition.

        The install-side override is synchronous and returns None, matching its
        contract. Binding does not register on FastAPI state and does not close
        anything. WorkerManager.startup subsequently calls register(celery_app).
        """
        if not self._enabled:
            return
        with self._lock:
            if self._tasks_factory is not None:
                raise PluginWorkerConfigurationError("plugin_job_tasks_source_ambiguous")
            if self._tasks is not None:
                if self._tasks is not tasks:
                    raise PluginWorkerConfigurationError(
                        "plugin_job_tasks_registry_conflict"
                    )
                return
            self._tasks = tasks
            self._resolve_tasks()

    def register(self, celery_app: Any) -> None:
        """Delegate the exact production registration contract to the registry."""
        if not self._enabled:
            return
        # Resolution plus delegation is serialized for API/CLI bootstrap races.
        # The registry itself retains its strong app references and fingerprints.
        with self._lock:
            if self._defer_until_worker_init:
                self._deferred_celery_app = celery_app
                self._register_worker_init_handler_locked()
                return
            self._resolve_tasks().register(celery_app)
        self._register_shutdown_handler()

    def _register_worker_init_handler_locked(self) -> None:
        """Resolve standalone resources only in a real Celery worker process."""

        if self._worker_init_registered:
            return
        import weakref

        from celery.signals import worker_process_init

        reference = weakref.ref(self)

        def initialize(**_kwargs: Any) -> None:
            registration = reference()
            if registration is None:
                return
            with registration._lock:
                app = registration._deferred_celery_app
                if app is None:
                    raise PluginWorkerConfigurationError("plugin_job_celery_app_required")
                registration._resolve_tasks().register(app)
            registration._register_shutdown_handler()

        worker_process_init.connect(
            initialize,
            weak=False,
            dispatch_uid=f"prodavan-plugin-jobs-init-{id(self)}",
        )
        self._worker_init_registered = True

    def _register_shutdown_handler(self) -> None:
        """Close only factory-owned CLI resources; API stays platform-owned."""

        if self._factory_owner is None or self._shutdown_registered:
            return
        import weakref

        from celery.signals import worker_process_shutdown

        reference = weakref.ref(self)

        def close(**_kwargs: Any) -> None:
            registration = reference()
            if registration is not None:
                from prodavan.core.jobs.async_runner import run_async

                run_async(registration.aclose())

        worker_process_shutdown.connect(
            close,
            weak=False,
            dispatch_uid=f"prodavan-plugin-jobs-close-{id(self)}",
        )
        self._shutdown_registered = True

    async def aclose(self) -> None:
        """Close the standalone factory owner once, after task deregistration."""

        if not self._enabled:
            return
        with self._lock:
            if self._closed or self._factory_owner is None:
                return
            self._closed = True
            tasks = self._tasks
            owner = self._factory_owner
        if tasks is not None:
            await tasks.aclose()
        await owner.aclose()


__all__ = [
    "PluginWorkerConfigurationError",
    "PluginWorkerRegistration",
    "PluginWorkerTasksFactory",
]
