"""Offline plugin-worker startup tests; intentionally not executed in freeze."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from celery import Celery

from prodavan.application.plugins.jobs.tasks import (
    ExecutionClaim,
    JobTaskConfigurationError,
    JobTaskSpec,
    ManifestJobTasks,
    StaleJobTaskDigest,
    job_id_for,
)
from prodavan.config.settings import settings
from prodavan.core.infra import worker_manager as wm
from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.plugin_worker import (
    PluginWorkerConfigurationError,
    PluginWorkerRegistration,
)

ARTIFACT_DIGEST = "sha256:" + "a" * 64
BACKEND_DIGEST = "sha256:" + "b" * 64
ARGUMENTS_DIGEST = "sha256:" + "c" * 64
RESULT_DIGEST = "sha256:" + "d" * 64
OTHER_DIGEST = "sha256:" + "e" * 64
OPERATION = "hello.jobs@1.0:refresh"
TASK_NAME = "plugin.core_worker_test.refresh"


def grant() -> Any:
    return SimpleNamespace(
        grant_id="grant_01",
        company_id="co_123",
        plugin_id="mod_x",
        plugin_instance_id="inst_abc",
        manifest_version="1.0.0",
        artifact_digest=ARTIFACT_DIGEST,
        is_active=True,
        revoked_at=None,
    )


def artifact(*, backend_digest: str = BACKEND_DIGEST) -> Any:
    return SimpleNamespace(
        plugin_id="mod_x",
        version="1.0.0",
        digest=ARTIFACT_DIGEST,
        published=True,
        verification=object(),
        publication=SimpleNamespace(backend_digest=backend_digest),
        manifest={
            "rpc_operations": [{
                "capability": {"id": "hello.jobs", "version": "1.0"},
                "operation": "refresh",
            }],
        },
    )


class GrantStore:
    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        return grant() if grant_id == "grant_01" else None


class ArtifactRegistry:
    def __init__(self, record: Any = None) -> None:
        self.record = record or artifact()

    async def by_digest_async(self, digest: str) -> Any:
        return self.record if digest == self.record.digest else None


class ExecutionStore:
    renew_interval_seconds = 60.0

    def __init__(self) -> None:
        self.records: dict[str, ExecutionClaim] = {}
        self.completed: dict[str, str] = {}
        self.releases = 0

    async def begin(self, identity: dict[str, str]) -> ExecutionClaim:
        claim = ExecutionClaim(
            job_id=identity["job_id"],
            owner="core-worker-test",
            binding_digest="sha256:" + "0" * 64,
        )
        self.records[claim.job_id] = claim
        return claim

    async def renew(self, claim: ExecutionClaim) -> None:
        if self.records.get(claim.job_id) != claim:
            from prodavan.application.plugins.jobs.tasks import JobTaskLeaseLost

            raise JobTaskLeaseLost()

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        self.completed[claim.job_id] = result_digest
        self.records.pop(claim.job_id, None)

    async def release(self, claim: ExecutionClaim) -> None:
        self.releases += 1
        self.records.pop(claim.job_id, None)


def make_tasks(
    handler: Any = None,
    *,
    artifacts: ArtifactRegistry | None = None,
    executions: ExecutionStore | None = None,
) -> ManifestJobTasks:
    return ManifestJobTasks(
        tasks={
            OPERATION: JobTaskSpec(
                operation=OPERATION,
                task_name=TASK_NAME,
                backend_digest=BACKEND_DIGEST,
                handler=handler or (lambda context: RESULT_DIGEST),
            ),
        },
        grant_store=GrantStore(),
        artifact_registry=artifacts or ArtifactRegistry(),
        execution_store=executions or ExecutionStore(),
        async_runner=asyncio.run,
    )


def execution_identity() -> dict[str, str]:
    return {
        "company_id": "co_123",
        "plugin_id": "mod_x",
        "plugin_instance_id": "inst_abc",
        "grant_id": "grant_01",
        "operation": OPERATION,
        "task_name": TASK_NAME,
        "run_id": "run1",
        "arguments_digest": ARGUMENTS_DIGEST,
        "artifact_digest": ARTIFACT_DIGEST,
        "backend_digest": BACKEND_DIGEST,
    }


def execution_kwargs() -> dict[str, str]:
    return {
        "company_id": "co_123",
        "plugin_id": "mod_x",
        "plugin_instance_id": "inst_abc",
        "grant_id": "grant_01",
        "run_id": "run1",
        "arguments_digest": ARGUMENTS_DIGEST,
    }


def configure_settings(monkeypatch: pytest.MonkeyPatch, *, platform: bool = True) -> None:
    values = {
        "plugin_platform_enabled": platform,
        "celery_enabled": True,
        "celery_broker_url": "memory://",
        "celery_result_backend": "memory://",
        "celery_task_always_eager": True,
        "trigger_worker_interval_sec": 5.0,
        "trigger_worker_enabled": True,
        "idle_pause_worker_enabled": True,
        "pod_reconcile_worker_enabled": False,
        "b2b_center_interval_sec": 900.0,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def celery_app(name: str) -> Celery:
    app = Celery(name, broker="memory://", backend="cache+memory://")
    app.conf.update(task_always_eager=True, task_eager_propagates=True)
    return app


@pytest.mark.asyncio
async def test_api_and_standalone_cli_register_same_real_handler_without_fastapi_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_settings(monkeypatch)
    monkeypatch.setattr(wm, "celery_app", None)
    monkeypatch.setattr(wm, "_manager", None)
    calls: list[str] = []
    executions = ExecutionStore()
    tasks = make_tasks(lambda context: calls.append(context.job_id) or RESULT_DIGEST, executions=executions)
    factory_calls = 0

    def tasks_factory() -> ManifestJobTasks:
        nonlocal factory_calls
        factory_calls += 1
        return tasks

    # This is the standalone -A/bootstrap path, not FastAPI lifespan/state.
    cli_app = wm.bootstrap_celery_app_from_settings(plugin_tasks_factory=tasks_factory)
    cli_manager = wm.get_worker_manager()
    assert factory_calls == 1
    assert cli_app.tasks[TASK_NAME]._plugin_job_registry is tasks
    assert job_names.TRIGGER_DRAIN in cli_app.tasks
    assert cli_app.conf.task_routes[job_names.INDEX_EQUIPMENT_CATALOG] == {"queue": "indexing"}
    assert "prodavan-trigger-drain" in cli_app.conf.beat_schedule
    assert "prodavan-idle-pause-sweep" in cli_app.conf.beat_schedule

    # API lifespan has its own manager/app, as a separate process would. Both
    # resolve the same server mapping but never manufacture another task wrapper.
    api_manager = wm.worker_manager_from_settings(plugin_tasks_factory=tasks_factory)
    await api_manager.startup()
    api_app = api_manager._app
    assert api_app is not cli_app
    assert factory_calls == 2
    assert api_app.tasks[TASK_NAME]._plugin_job_registry is tasks
    assert api_app.tasks[TASK_NAME]._plugin_job_operation == OPERATION

    # Repeating either startup hook is a no-op for the same app and factory.
    api_manager._plugin_worker.register(api_app)
    cli_manager._plugin_worker.register(cli_app)
    assert factory_calls == 2

    job_id = job_id_for(execution_identity())
    result = await asyncio.to_thread(
        lambda: cli_app.tasks[TASK_NAME].apply(
            kwargs=execution_kwargs(), task_id=job_id
        ).get()
    )
    assert result == RESULT_DIGEST
    assert calls == [job_id]
    assert executions.completed == {job_id: RESULT_DIGEST}

    # Worker shutdown drops its app only; the composition owner retains registry.
    await api_manager.shutdown()
    assert tasks._closed is False


def test_no_mapping_is_unwired_and_does_not_discover_api_or_global_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_settings(monkeypatch)
    api_only_tasks = make_tasks()
    api_only_app = celery_app("api-only")
    api_only_tasks.register(api_only_app)

    manager = wm.worker_manager_from_settings()
    app = manager._build_app()

    assert TASK_NAME in api_only_app.tasks
    assert TASK_NAME not in app.tasks
    assert manager._plugin_worker.wired is False
    assert manager._plugin_worker.enabled is False
    assert job_names.TRIGGER_DRAIN in app.tasks
    assert "prodavan-trigger-drain" in app.conf.beat_schedule


def test_enabled_jobs_without_registry_fail_but_platform_disable_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_settings(monkeypatch)
    missing = wm.worker_manager_from_settings(plugin_jobs_enabled=True)
    with pytest.raises(PluginWorkerConfigurationError) as caught:
        missing._build_app()
    assert caught.value.code == "plugin_job_tasks_registry_or_factory_required"

    configure_settings(monkeypatch, platform=False)
    factory_calls = 0

    def forbidden_factory() -> ManifestJobTasks:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("disabled platform must not resolve plugin jobs")

    disabled = wm.worker_manager_from_settings(
        plugin_jobs_enabled=True,
        plugin_tasks_factory=forbidden_factory,
    )
    app = disabled._build_app()
    assert factory_calls == 0
    assert TASK_NAME not in app.tasks
    assert job_names.TRIGGER_DRAIN in app.tasks


def test_install_registration_hook_binds_existing_registry_once() -> None:
    tasks = make_tasks()
    hook = PluginWorkerRegistration(enabled=True)
    hook.registration_hook(tasks)
    hook.registration_hook(tasks)
    assert hook.wired is True

    app = celery_app("install-hook")
    hook.register(app)
    assert app.tasks[TASK_NAME]._plugin_job_registry is tasks

    with pytest.raises(PluginWorkerConfigurationError) as caught:
        hook.registration_hook(make_tasks())
    assert caught.value.code == "plugin_job_tasks_registry_conflict"


def test_repeat_registration_is_idempotent_and_foreign_name_conflicts() -> None:
    tasks = make_tasks()
    hook = PluginWorkerRegistration(enabled=True, tasks=tasks)
    app = celery_app("repeat")
    hook.register(app)
    first = app.tasks[TASK_NAME]
    hook.register(app)
    assert app.tasks[TASK_NAME] is first

    conflict = celery_app("conflict")
    conflict.tasks[TASK_NAME] = object()
    with pytest.raises(JobTaskConfigurationError, match="job_celery_task_name_conflict"):
        hook.register(conflict)


def test_registered_worker_preserves_digest_fencing_before_side_effect() -> None:
    calls: list[Any] = []
    executions = ExecutionStore()
    tasks = make_tasks(
        lambda context: calls.append(context) or RESULT_DIGEST,
        artifacts=ArtifactRegistry(artifact(backend_digest=OTHER_DIGEST)),
        executions=executions,
    )
    app = celery_app("digest-fence")
    PluginWorkerRegistration(enabled=True, tasks=tasks).register(app)

    job_id = job_id_for(execution_identity())
    with pytest.raises(StaleJobTaskDigest, match="job_backend_digest_stale"):
        app.tasks[TASK_NAME].apply(kwargs=execution_kwargs(), task_id=job_id).get()

    assert calls == []
    assert executions.records == {}
    assert executions.completed == {}
    assert executions.releases == 0
