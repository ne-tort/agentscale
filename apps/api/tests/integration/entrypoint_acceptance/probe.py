"""Sanitized subprocess probes; never monkeypatches application state or dependencies."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import uuid
from typing import Any


def _emit(**values: Any) -> None:
    print(json.dumps({"ok": True, **values}, sort_keys=True, separators=(",", ":")), flush=True)


def _running_ttl_renewed(
    record: Any,
    raw_ttl: Any,
    previous_ttl: int | None,
    execution_done: bool,
) -> bool:
    """Count only a growing PTTL on a still-running execution, never completion."""
    if execution_done or not isinstance(record, dict) or record.get("state") != "running":
        return False
    return isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) and raw_ttl > 0 and (
        previous_ttl is not None and raw_ttl > previous_ttl
    )


def _composition(entrypoint: str) -> None:
    if entrypoint not in {"main", "main_plugin_gateway"}:
        raise ValueError("unknown entrypoint")
    module = importlib.import_module("prodavan." + entrypoint)
    app = module.app
    from prodavan.application.plugins.capabilities.payloads import DigestPayloadStore
    from prodavan.application.plugins.jobs.tasks import ManifestJobTasks
    from prodavan.application.plugins.wiring.adapters import WIRED_CAPABILITIES, adapter_report

    platform = app.state.plugin_platform
    registry = app.state.plugin_adapter_registry
    report = adapter_report(registry)
    assert set(report["registered"]) == set(WIRED_CAPABILITIES)
    assert set(report["real_adapters"]) == set(WIRED_CAPABILITIES)
    assert report["stub_count"] == 0 and report["db_direct_registered"] is False
    assert app.state.plugin_unwired_ports == ()
    assert app.state.plugin_in_memory_ports == ()
    payload = app.state.plugin_digest_payload_store
    assert isinstance(payload, DigestPayloadStore)
    jobs = registry.resolve("jobs")
    tasks = jobs._allowed_tasks
    assert isinstance(tasks, ManifestJobTasks)
    assert tasks._grants is platform.admin._ports.grant_store
    # ManifestJobTasks captures the SQL store's bound by_digest method as the
    # closure default; PluginArtifactRegistry itself has no by_digest method.
    reader = tasks._artifact_reader
    assert reader.__defaults__ is not None and len(reader.__defaults__) == 1
    assert reader.__defaults__[0].__self__ is platform.artifact_registry._store
    assert app.state.plugin_event_dispatcher._signer is not None
    specs = {spec.operation: spec.task_name for spec in tasks.specs}
    assert os.environ["ENTRYPOINT_TEST_JOB_OPERATION"] in specs
    assert os.environ["ENTRYPOINT_TEST_SLOW_JOB_OPERATION"] in specs
    from prodavan.core.infra.worker_manager import WorkerManager
    from prodavan.core.wiring import get_lifespan_manager

    lifespan = get_lifespan_manager()
    manager = lifespan.get("worker") if lifespan is not None else None
    assert isinstance(manager, WorkerManager)
    assert manager._plugin_worker is not None
    assert manager._plugin_worker.wired is True
    _emit(
        entrypoint=entrypoint,
        adapters={name: type(registry.resolve(name)).__name__ for name in WIRED_CAPABILITIES},
        payload_store=type(payload).__name__,
        manifest_tasks=type(tasks).__name__,
        task_specs=specs,
        declarations=os.environ["PLUGIN_JOB_DECLARATIONS"],
        repositories={
            "grants": type(tasks._grants).__name__,
            "artifacts": type(platform.artifact_registry._store).__name__,
        },
        worker_wired=True,
        unwired=[],
        in_memory=[],
    )


def _worker_deferred() -> None:
    from prodavan.application.plugins.jobs.declarations import (
        DeferredStandalonePluginJobComposition,
    )
    from prodavan.core.infra.worker_manager import get_worker_manager

    manager = get_worker_manager()
    registration = manager._plugin_worker if manager is not None else None
    assert registration is not None and registration.enabled
    assert registration._defer_until_worker_init is True
    assert registration.wired is False
    factory = registration._tasks_factory
    assert isinstance(factory, DeferredStandalonePluginJobComposition)
    assert factory._composition is None
    _emit(
        deferred=True,
        factory=type(factory).__name__,
        allocated=False,
        declarations=os.environ["PLUGIN_JOB_DECLARATIONS"],
    )


async def _worker_ping() -> None:
    from prodavan.core.infra.worker_manager import get_celery_app

    app = get_celery_app()
    assert app.conf.task_always_eager is not True
    result = await asyncio.to_thread(app.control.inspect(timeout=3.0).ping)
    assert isinstance(result, dict) and result
    _emit(worker_pong=sorted(result))


def _lock_identity() -> dict[str, str]:
    return {
        "company_id": os.environ["ENTRYPOINT_TEST_COMPANY_ID"],
        "plugin_id": os.environ["ENTRYPOINT_TEST_PLUGIN_ID"],
        "plugin_instance_id": os.environ["ENTRYPOINT_TEST_INSTANCE_ID"],
        "expected_manifest_version": os.environ["ENTRYPOINT_TEST_VERSION"],
        "expected_artifact_digest": os.environ["ENTRYPOINT_TEST_ARTIFACT_DIGEST"],
    }


async def _update_lock_worker(mode: str) -> None:
    from prodavan.infrastructure.persistence.database import dispose_engine, get_session_factory
    from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
        SqlPluginUpdateLock,
    )

    try:
        lock = SqlPluginUpdateLock(session_factory=get_session_factory())
        if mode == "hold":
            lease = await lock.acquire(**_lock_identity())
            assert lease is not None
            print(json.dumps({"state": "acquired"}), flush=True)
            await asyncio.sleep(1.0)
            await lock.release(lease)
            _emit(owner_released=True)
            return
        contended = False
        lease = await lock.acquire(**_lock_identity())
        if lease is None:
            contended = True
            for _ in range(100):
                await asyncio.sleep(0.05)
                lease = await lock.acquire(**_lock_identity())
                if lease is not None:
                    break
        assert lease is not None, "lock was not acquired after owner release"
        await lock.release(lease)
        _emit(contended=contended, acquired_after_release=True)
    finally:
        await dispose_engine()


async def _update_lock_race() -> None:
    async def child(mode: str) -> Any:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m", "tests.integration.entrypoint_acceptance.probe",
            "update-lock-worker", mode,
            cwd=os.getcwd(), env=dict(os.environ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

    owner = await child("hold")
    contender = None
    try:
        assert owner.stdout is not None
        first = json.loads((await asyncio.wait_for(owner.stdout.readline(), 5.0)).decode())
        assert first == {"state": "acquired"}
        contender = await child("contend")
        owner_result, contender_result = await asyncio.gather(
            asyncio.wait_for(owner.wait(), 15.0),
            asyncio.wait_for(contender.wait(), 15.0),
        )
        assert owner_result == 0 and contender_result == 0
        _emit(two_processes=True, contender_blocked=True, owner_released=True)
    finally:
        for process in (owner, contender):
            if process is not None and process.returncode is None:
                process.kill()
        await asyncio.gather(
            *(process.wait() for process in (owner, contender) if process is not None),
            return_exceptions=True,
        )


async def _lease_renewal() -> None:
    from prodavan.application.plugins.jobs.declarations import (
        DeferredStandalonePluginJobComposition,
    )
    from prodavan.application.plugins.jobs.tasks import (
        JobTaskRetryRequired,
        ManifestJobTasks,
        RedisJobExecutionStore,
        job_id_for,
    )
    from prodavan.config.settings import settings
    from prodavan.core.jobs.async_runner import run_async
    from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

    deferred = DeferredStandalonePluginJobComposition(settings)
    short_tasks = None
    try:
        owner_tasks = deferred()
        composition = deferred._composition
        assert composition is not None
        specs = composition.task_specs
        operation = os.environ["ENTRYPOINT_TEST_SLOW_JOB_OPERATION"]
        spec = specs[operation]
        store = RedisJobExecutionStore(
            redis=ManagerRedisClient(composition._redis),
            lease_seconds=30,
            retention_seconds=60,
            renew_interval_seconds=0.05,
        )
        short_tasks = ManifestJobTasks(
            tasks=specs,
            grant_store=composition._repositories.grant_store,
            artifact_registry=composition._repositories.artifact_store,
            execution_store=store,
            async_runner=run_async,
        )
        identity = {
            "company_id": os.environ["ENTRYPOINT_TEST_COMPANY_ID"],
            "plugin_id": os.environ["ENTRYPOINT_TEST_PLUGIN_ID"],
            "plugin_instance_id": os.environ["ENTRYPOINT_TEST_INSTANCE_ID"],
            "grant_id": os.environ["ENTRYPOINT_TEST_GRANT_ID"],
            "operation": operation,
            "task_name": spec.task_name,
            "run_id": "lease_" + uuid.uuid4().hex,
            "arguments_digest": "sha256:" + "0" * 64,
            "artifact_digest": os.environ["ENTRYPOINT_TEST_ARTIFACT_DIGEST"],
            "backend_digest": spec.backend_digest,
        }
        identity["job_id"] = job_id_for(identity)
        kwargs = {
            name: identity[name]
            for name in (
                "company_id", "plugin_id", "plugin_instance_id", "grant_id",
                "run_id", "arguments_digest",
            )
        }
        execution = asyncio.create_task(short_tasks.execute(
            task_name=spec.task_name, job_id=identity["job_id"], kwargs=kwargs
        ))
        key = store._key(identity["job_id"])
        previous_ttl: int | None = None
        renewals = 0
        contender_denied = False
        for _ in range(400):
            raw_record = await composition._redis.client.get(key)
            raw_ttl = await composition._redis.client.pttl(key)
            record = json.loads(raw_record) if raw_record is not None else None
            renewed = _running_ttl_renewed(record, raw_ttl, previous_ttl, execution.done())
            if renewed:
                renewals += 1
            if (
                isinstance(record, dict) and record.get("state") == "running"
                and isinstance(raw_ttl, int) and raw_ttl > 0
            ):
                previous_ttl = raw_ttl
                if not contender_denied:
                    contender = RedisJobExecutionStore(
                        redis=ManagerRedisClient(composition._redis),
                        lease_seconds=30,
                        retention_seconds=60,
                        renew_interval_seconds=0.05,
                    )
                    try:
                        await contender.begin(identity)
                    except JobTaskRetryRequired:
                        contender_denied = True
            if execution.done():
                break
            if renewals >= 2 and contender_denied:
                break
            await asyncio.sleep(0.025)
        if execution.done() and renewals < 2:
            await execution
            raise AssertionError("real scoped slow operation ended before two lease renewals")
        assert renewals >= 2, f"only {renewals} Redis lease renewals observed"
        assert contender_denied
        result = await asyncio.wait_for(execution, timeout=30.0)
        assert result.startswith("sha256:")
        record = json.loads(await composition._redis.client.get(key))
        assert record["state"] == "completed" and record["result_digest"] == result
        assert await composition._redis.client.ttl(key) > 0
        _emit(renewals=renewals, contender_denied=True, result_digest=result)
    finally:
        if short_tasks is not None:
            await short_tasks.aclose()
        await deferred.aclose()
        await deferred.aclose()
        if deferred._composition is not None:
            assert deferred._composition._closed is True


async def _dispatch(arguments: list[str]) -> None:
    if not arguments:
        raise ValueError("probe mode is required")
    mode = arguments[0]
    if mode == "composition" and len(arguments) == 2:
        _composition(arguments[1])
    elif mode == "worker-deferred":
        _worker_deferred()
    elif mode == "worker-ping":
        await _worker_ping()
    elif mode == "update-lock-worker" and len(arguments) == 2:
        await _update_lock_worker(arguments[1])
    elif mode == "update-lock-race":
        await _update_lock_race()
    elif mode == "lease-renewal":
        await _lease_renewal()
    else:
        raise ValueError("unknown probe arguments")


def main() -> int:
    try:
        asyncio.run(_dispatch(sys.argv[1:]))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": type(exc).__name__,
        }, sort_keys=True, separators=(",", ":")), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
