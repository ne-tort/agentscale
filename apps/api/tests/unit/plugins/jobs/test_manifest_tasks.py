"""Manifest allowlist, digest fencing, dedup and Celery registration tests.

Written during the migration verification freeze; intentionally not executed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
)
from prodavan.application.plugins.capabilities.contracts import empty_digest
from prodavan.application.plugins.capabilities.adapters.jobs import CeleryJobsAdapter
from prodavan.application.plugins.jobs.tasks import (
    JobTaskAccessDenied,
    JobTaskConfigurationError,
    JobTaskExecutionError,
    JobTaskRetryRequired,
    StaleJobTaskDigest,
    job_id_for,
)
from .conftest import (
    ARGUMENTS_DIGEST,
    ARTIFACT_DIGEST,
    BACKEND_DIGEST,
    OPERATION,
    OTHER_DIGEST,
    RESULT_DIGEST,
    TASK_NAME,
    ArtifactRegistry,
    ExecutionStore,
    artifact,
    make_tasks,
    request,
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


class Queue:
    def __init__(self) -> None:
        self.identities: list[dict[str, str]] = []

    async def submit(self, *, identity: dict[str, str]) -> dict[str, Any]:
        self.identities.append(dict(identity))
        return {
            **identity,
            "state": "pending",
            "result_digest": empty_digest(),
            "lease": {
                "lease_id": "lease-1",
                "lease_owner": "worker-1",
                "expires_at_epoch": 2000000000,
            },
        }

    async def get(self, *, identity: dict[str, str]) -> dict[str, Any]:
        raise AssertionError("not used")

    async def cancel(self, *, identity: dict[str, str]) -> dict[str, Any]:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_adapter_maps_only_manifest_operation_to_concrete_task_and_digest() -> None:
    tasks = make_tasks()
    queue = Queue()
    adapter = CeleryJobsAdapter(queue=queue, allowed_tasks=tasks)

    response = await adapter.submit(request())
    meter = await adapter.quota_usage(request())

    sent = queue.identities[0]
    assert response.job_id == sent["job_id"]
    assert sent["operation"] == OPERATION
    assert sent["task_name"] == TASK_NAME
    assert sent["backend_digest"] == BACKEND_DIGEST
    assert sent["artifact_digest"] == ARTIFACT_DIGEST
    assert meter.ops == meter.rows == meter.keys == 1
    assert meter.bytes > 0

    with pytest.raises(CapabilityAccessDenied, match="job_task_not_allowed"):
        await adapter.submit(request(operation="hello.jobs@1.0:missing"))
    assert len(queue.identities) == 1


@pytest.mark.asyncio
async def test_manifest_declaration_is_mandatory_even_for_server_mapped_task() -> None:
    tasks = make_tasks(artifacts=ArtifactRegistry(artifact(operations=[])))
    with pytest.raises(CapabilityAccessDenied, match="job_operation_not_declared"):
        await CeleryJobsAdapter(queue=Queue(), allowed_tasks=tasks).submit(request())


@pytest.mark.asyncio
async def test_stale_backend_digest_fails_before_any_side_effect() -> None:
    calls = []
    tasks = make_tasks(
        lambda context: calls.append(context) or RESULT_DIGEST,
        artifacts=ArtifactRegistry(artifact(backend_digest=OTHER_DIGEST)),
    )
    with pytest.raises(StaleJobTaskDigest, match="job_backend_digest_stale"):
        await tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_handler_digest_is_real_and_completed_execution_deduplicates() -> None:
    calls = []
    executions = ExecutionStore()
    tasks = make_tasks(
        lambda context: calls.append(context.job_id) or RESULT_DIGEST,
        executions=executions,
    )
    job_id = job_id_for(execution_identity())

    first = await tasks.execute(
        task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs()
    )
    second = await tasks.execute(
        task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs()
    )

    assert first == second == RESULT_DIGEST
    assert calls == [job_id]
    assert executions.completed[job_id] == RESULT_DIGEST


@pytest.mark.asyncio
async def test_handler_payload_is_not_converted_into_synthetic_task_result() -> None:
    executions = ExecutionStore()
    tasks = make_tasks(lambda context: {"raw": "result"}, executions=executions)
    with pytest.raises(JobTaskExecutionError, match="job_task_result_digest_required"):
        await tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    assert executions.completed == {}
    assert executions.releases == 1


@pytest.mark.asyncio
async def test_worker_kwargs_are_bound_to_exact_grant_instance() -> None:
    calls = []
    tasks = make_tasks(lambda context: calls.append(context) or RESULT_DIGEST)
    kwargs = {**execution_kwargs(), "company_id": "co_other"}
    with pytest.raises(JobTaskAccessDenied, match="job_identity_binding_mismatch"):
        await tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=kwargs,
        )
    assert calls == []


class FakeCelery:
    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {}
        self.options: list[dict[str, Any]] = []

    def task(self, **options: Any) -> Any:
        self.options.append(options)
        def decorate(function: Any) -> Any:
            self.tasks[options["name"]] = function
            return function
        return decorate


def test_celery_retries_only_pre_side_effect_lease_conflicts() -> None:
    class ScheduledRetry(Exception):
        pass

    retry_calls = []

    def retry(**kwargs: Any) -> Exception:
        retry_calls.append(kwargs)
        return ScheduledRetry()

    bound = SimpleNamespace(
        request=SimpleNamespace(id="job_retry", retries=0), retry=retry
    )

    def lease_conflict(coroutine: Any) -> str:
        coroutine.close()
        raise JobTaskRetryRequired("job_execution_already_running")

    tasks = make_tasks(async_runner=lease_conflict)
    app = FakeCelery()
    tasks.register(app)
    with pytest.raises(ScheduledRetry):
        app.tasks[TASK_NAME](bound, **execution_kwargs())
    assert retry_calls[0]["max_retries"] == 2

    def stale(coroutine: Any) -> str:
        coroutine.close()
        raise StaleJobTaskDigest()

    stale_tasks = make_tasks(async_runner=stale)
    stale_app = FakeCelery()
    stale_tasks.register(stale_app)
    with pytest.raises(StaleJobTaskDigest):
        stale_app.tasks[TASK_NAME](bound, **execution_kwargs())
    assert len(retry_calls) == 1


@pytest.mark.asyncio
async def test_celery_registration_is_idempotent_conflict_free_and_close_once() -> None:
    tasks = make_tasks(async_runner=lambda coroutine: RESULT_DIGEST)
    app = FakeCelery()
    tasks.register(app)
    tasks.register(app)
    assert list(app.tasks) == [TASK_NAME]
    assert app.options[0]["acks_late"] is True
    assert app.options[0]["max_retries"] == 2
    assert app.options[0]["shared"] is False

    conflict = FakeCelery()
    conflict.tasks[TASK_NAME] = object()
    with pytest.raises(JobTaskConfigurationError, match="job_celery_task_name_conflict"):
        tasks.register(conflict)

    await tasks.aclose()
    await tasks.aclose()
    with pytest.raises(JobTaskExecutionError, match="job_task_registry_closed"):
        await tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )


def test_celery_registration_does_not_leak_into_later_independent_apps() -> None:
    from celery import Celery

    first_tasks = make_tasks(async_runner=lambda coroutine: RESULT_DIGEST)
    first_app = Celery("manifest-registration-first")
    first_tasks.register(first_app)

    second_tasks = make_tasks(async_runner=lambda coroutine: RESULT_DIGEST)
    second_app = Celery("manifest-registration-second")
    assert TASK_NAME not in second_app.tasks
    second_tasks.register(second_app)
    second_tasks.register(second_app)

    assert first_app.tasks[TASK_NAME]._plugin_job_registry is first_tasks
    assert second_app.tasks[TASK_NAME]._plugin_job_registry is second_tasks

    foreign_tasks = make_tasks(async_runner=lambda coroutine: RESULT_DIGEST)
    with pytest.raises(JobTaskConfigurationError, match="job_celery_task_name_conflict"):
        foreign_tasks.register(second_app)
