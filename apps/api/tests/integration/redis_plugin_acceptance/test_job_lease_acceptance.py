"""Real-Redis execution lease acceptance; no Redis behavior is emulated."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.jobs.tasks import (
    ExecutionClaim,
    JobTaskExecutionUncertain,
    JobTaskLeaseLost,
    JobTaskRetryRequired,
    JobTaskSpec,
    ManifestJobTasks,
    RedisJobExecutionStore,
    job_id_for,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.redis_acceptance]

ARTIFACT_DIGEST = "sha256:" + "a" * 64
BACKEND_DIGEST = "sha256:" + "b" * 64
ARGUMENTS_DIGEST = "sha256:" + "c" * 64
RESULT_DIGEST = "sha256:" + "d" * 64
OPERATION = "hello.jobs@1.0:refresh"
TASK_NAME = "plugin.safe.task"


def execution_identity(run_id: str = "run-acceptance") -> dict[str, str]:
    identity = {
        "company_id": "co_redis_acceptance",
        "plugin_id": "mod_redis_acceptance",
        "plugin_instance_id": "inst_redis_acceptance",
        "grant_id": "grant_redis_acceptance",
        "operation": OPERATION,
        "task_name": TASK_NAME,
        "run_id": run_id,
        "arguments_digest": ARGUMENTS_DIGEST,
        "artifact_digest": ARTIFACT_DIGEST,
        "backend_digest": BACKEND_DIGEST,
    }
    identity["job_id"] = job_id_for(identity)
    return identity


def execution_kwargs(run_id: str = "run-acceptance") -> dict[str, str]:
    return {
        "company_id": "co_redis_acceptance",
        "plugin_id": "mod_redis_acceptance",
        "plugin_instance_id": "inst_redis_acceptance",
        "grant_id": "grant_redis_acceptance",
        "run_id": run_id,
        "arguments_digest": ARGUMENTS_DIGEST,
    }


class JobGrantStore:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            grant_id="grant_redis_acceptance",
            company_id="co_redis_acceptance",
            plugin_id="mod_redis_acceptance",
            plugin_instance_id="inst_redis_acceptance",
            manifest_version="1.0.0",
            artifact_digest=ARTIFACT_DIGEST,
            is_active=True,
            revoked_at=None,
        )

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return self.record if grant_id == self.record.grant_id else None


class JobArtifactRegistry:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            plugin_id="mod_redis_acceptance",
            version="1.0.0",
            digest=ARTIFACT_DIGEST,
            published=True,
            verification=object(),
            publication=SimpleNamespace(backend_digest=BACKEND_DIGEST),
            manifest={
                "rpc_operations": [{
                    "capability": {"id": "hello.jobs", "version": "1.0"},
                    "operation": "refresh",
                    "http_method": "POST",
                    "backend_path": "/api/jobs/refresh",
                    "public": False,
                }],
            },
        )

    async def by_digest_async(self, digest: str) -> Any | None:
        return self.record if digest == self.record.digest else None


class ObservedRedisJobExecutionStore(RedisJobExecutionStore):
    """Fault/observation wrapper which delegates every real state change to Redis."""

    def __init__(self, *, redis: Any) -> None:
        super().__init__(
            redis=redis,
            lease_seconds=30,
            retention_seconds=60,
            renew_interval_seconds=0.05,
        )
        self.renew_attempted = asyncio.Event()
        self.renewed = asyncio.Event()
        self.renew_calls = 0
        self.successful_renewals = 0
        self.release_calls = 0
        self.completion_calls = 0
        self.fail_renew = False

    async def renew(self, claim: ExecutionClaim) -> None:
        self.renew_calls += 1
        self.renew_attempted.set()
        if self.fail_renew:
            raise JobTaskExecutionUncertain("job_execution_renew_failed")
        await super().renew(claim)
        self.successful_renewals += 1
        self.renewed.set()

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        self.completion_calls += 1
        await super().complete(claim, result_digest=result_digest)

    async def release(self, claim: ExecutionClaim) -> None:
        self.release_calls += 1
        await super().release(claim)


def make_tasks(handler: Any, store: ObservedRedisJobExecutionStore) -> ManifestJobTasks:
    return ManifestJobTasks(
        tasks={
            OPERATION: JobTaskSpec(
                operation=OPERATION,
                task_name=TASK_NAME,
                backend_digest=BACKEND_DIGEST,
                handler=handler,
            ),
        },
        grant_store=JobGrantStore(),
        artifact_registry=JobArtifactRegistry(),
        execution_store=store,
    )


async def wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=3.0)


async def running_record(redis: Any, store: RedisJobExecutionStore, job_id: str) -> dict[str, Any]:
    raw = await redis.get(store._key(job_id))
    assert raw is not None, "the running lease must not be released"
    record = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    assert record["state"] == "running"
    return record


async def test_nx_running_completed_owner_binding_cas_and_concurrent_denial(
    redis_pair: Any,
) -> None:
    first = RedisJobExecutionStore(
        redis=redis_pair.api, lease_seconds=30, retention_seconds=60,
        renew_interval_seconds=1.0,
    )
    second = RedisJobExecutionStore(
        redis=redis_pair.gateway, lease_seconds=30, retention_seconds=60,
        renew_interval_seconds=1.0,
    )
    identity = execution_identity("run-concurrent")

    outcomes = await asyncio.gather(
        first.begin(identity), second.begin(identity), return_exceptions=True,
    )
    claims = [item for item in outcomes if isinstance(item, ExecutionClaim)]
    conflicts = [item for item in outcomes if isinstance(item, JobTaskRetryRequired)]
    assert len(claims) == 1
    assert len(conflicts) == 1
    assert conflicts[0].code == "job_execution_already_running"
    claim = claims[0]
    owner_store = first if outcomes[0] is claim else second
    contender_store = second if owner_store is first else first
    contender_redis = redis_pair.gateway if owner_store is first else redis_pair.api

    # The contender really received redis.asyncio's NX miss (None), then used GET.
    assert contender_redis.nx_results == [None]
    record = await running_record(contender_redis, first, claim.job_id)
    assert record["owner"] == claim.owner
    assert record["binding_digest"] == claim.binding_digest

    for mismatched in (
        replace(claim, owner="foreign-owner"),
        replace(claim, binding_digest="sha256:" + "0" * 64),
    ):
        with pytest.raises(JobTaskLeaseLost):
            await owner_store.renew(mismatched)
        with pytest.raises(JobTaskLeaseLost):
            await owner_store.complete(mismatched, result_digest=RESULT_DIGEST)
        await running_record(contender_redis, first, claim.job_id)

    await owner_store.renew(claim)
    assert await contender_redis.pttl(first._key(claim.job_id)) > 0
    await owner_store.complete(claim, result_digest=RESULT_DIGEST)

    # Another NX=None -> GET now observes the durable completed result.
    deduplicated = await contender_store.begin(identity)
    assert deduplicated.is_new is False
    assert deduplicated.owner == ""
    assert deduplicated.result_digest == RESULT_DIGEST
    assert contender_redis.nx_results[-1] is None
    with pytest.raises(JobTaskLeaseLost):
        await owner_store.renew(claim)
    raw = await contender_redis.get(first._key(claim.job_id))
    completed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    assert completed["state"] == "completed"
    assert completed["result_digest"] == RESULT_DIGEST


async def test_post_validation_renewal_blocks_second_owner_and_deduplicates(
    redis_pair: Any,
) -> None:
    store = ObservedRedisJobExecutionStore(redis=redis_pair.api)
    contender = RedisJobExecutionStore(
        redis=redis_pair.gateway, lease_seconds=30, retention_seconds=60,
        renew_interval_seconds=1.0,
    )
    identity = execution_identity("run-post-validation")
    validation_started = asyncio.Event()
    finish_validation = asyncio.Event()
    handler_calls: list[str] = []

    async def handler(context: Any) -> str:
        handler_calls.append(context.job_id)
        return RESULT_DIGEST

    tasks = make_tasks(handler, store)
    original_context = tasks._context
    context_calls = 0

    async def blocked_post_validation(spec: Any, *, job_id: str, kwargs: Any) -> Any:
        nonlocal context_calls
        context_calls += 1
        if context_calls == 1:
            return await original_context(spec, job_id=job_id, kwargs=kwargs)
        validation_started.set()
        await finish_validation.wait()
        return await original_context(spec, job_id=job_id, kwargs=kwargs)

    tasks._context = blocked_post_validation
    execution = asyncio.create_task(tasks.execute(
        task_name=TASK_NAME, job_id=identity["job_id"], kwargs=execution_kwargs(
            "run-post-validation"
        ),
    ))
    try:
        await wait(validation_started)
        store.renewed.clear()
        renewals_before = store.successful_renewals
        await wait(store.renewed)
        assert store.successful_renewals > renewals_before
        assert store.completion_calls == 0
        with pytest.raises(JobTaskRetryRequired, match="job_execution_already_running"):
            await contender.begin(identity)
        assert redis_pair.gateway.nx_results[-1] is None
        assert store.release_calls == 0
    finally:
        finish_validation.set()
    assert await asyncio.wait_for(execution, timeout=3.0) == RESULT_DIGEST
    assert store.completion_calls == 1
    assert store.release_calls == 0

    # Handler output, not a fabricated success, is returned for every replay.
    assert await tasks.execute(
        task_name=TASK_NAME, job_id=identity["job_id"], kwargs=execution_kwargs(
            "run-post-validation"
        ),
    ) == RESULT_DIGEST
    assert handler_calls == [identity["job_id"]]
    replay = await contender.begin(identity)
    assert replay.is_new is False
    assert replay.result_digest == RESULT_DIGEST
    await tasks.aclose()


async def test_renewal_uncertainty_keeps_running_lease_and_disallows_retry(
    redis_pair: Any,
) -> None:
    store = ObservedRedisJobExecutionStore(redis=redis_pair.api)
    store.fail_renew = True
    contender = RedisJobExecutionStore(
        redis=redis_pair.gateway, lease_seconds=30, retention_seconds=60,
        renew_interval_seconds=1.0,
    )
    identity = execution_identity("run-uncertain")
    handler_started = asyncio.Event()
    handler_cleaned = asyncio.Event()
    handler_calls = 0

    async def handler(_context: Any) -> str:
        nonlocal handler_calls
        handler_calls += 1
        handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cleaned.set()
        return RESULT_DIGEST

    tasks = make_tasks(handler, store)
    execution = asyncio.create_task(tasks.execute(
        task_name=TASK_NAME, job_id=identity["job_id"],
        kwargs=execution_kwargs("run-uncertain"),
    ))
    await wait(handler_started)
    await wait(store.renew_attempted)
    with pytest.raises(JobTaskExecutionUncertain, match="job_execution_renew_failed"):
        await asyncio.wait_for(execution, timeout=3.0)
    await wait(handler_cleaned)

    assert handler_calls == 1
    assert store.release_calls == 0
    assert store.completion_calls == 0
    await running_record(redis_pair.gateway, store, identity["job_id"])
    # The typed uncertainty is not a retryable pre-side-effect conflict.
    with pytest.raises(JobTaskRetryRequired, match="job_execution_already_running"):
        await contender.begin(identity)
    assert handler_calls == 1
    await tasks.aclose()


async def test_external_cancellation_keeps_running_lease_and_disallows_retry(
    redis_pair: Any,
) -> None:
    store = ObservedRedisJobExecutionStore(redis=redis_pair.api)
    contender = RedisJobExecutionStore(
        redis=redis_pair.gateway, lease_seconds=30, retention_seconds=60,
        renew_interval_seconds=1.0,
    )
    identity = execution_identity("run-cancelled")
    handler_started = asyncio.Event()
    handler_cleaned = asyncio.Event()
    handler_calls = 0

    async def handler(_context: Any) -> str:
        nonlocal handler_calls
        handler_calls += 1
        handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cleaned.set()
        return RESULT_DIGEST

    tasks = make_tasks(handler, store)
    execution = asyncio.create_task(tasks.execute(
        task_name=TASK_NAME, job_id=identity["job_id"],
        kwargs=execution_kwargs("run-cancelled"),
    ))
    await wait(handler_started)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    await wait(handler_cleaned)

    renewals = store.renew_calls
    await asyncio.sleep(0.12)
    assert store.renew_calls == renewals, "the cancelled watchdog must stay stopped"
    assert handler_calls == 1
    assert store.release_calls == 0
    assert store.completion_calls == 0
    await running_record(redis_pair.gateway, store, identity["job_id"])
    with pytest.raises(JobTaskRetryRequired, match="job_execution_already_running"):
        await contender.begin(identity)
    assert handler_calls == 1
    await tasks.aclose()
