"""Controllable execution-lease/watchdog tests; no live Redis or wall-clock waits."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.jobs.tasks import (
    ExecutionClaim,
    JobTaskConfigurationError,
    JobTaskExecutionError,
    JobTaskExecutionUncertain,
    JobTaskLeaseLost,
    JobTaskRetryRequired,
    RedisJobExecutionStore,
    job_id_for,
)
from .conftest import (
    ARGUMENTS_DIGEST,
    ARTIFACT_DIGEST,
    BACKEND_DIGEST,
    OPERATION,
    RESULT_DIGEST,
    TASK_NAME,
    make_tasks,
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


def identity_with_job_id() -> dict[str, str]:
    identity = execution_identity()
    identity["job_id"] = job_id_for(identity)
    return identity


class ControlledExecutionStore:
    renew_interval_seconds = 0.005
    ttl_seconds = 2.0

    def __init__(self) -> None:
        self.now = 0.0
        self.current: ExecutionClaim | None = None
        self.expires_at = 0.0
        self.owner_sequence = 0
        self.completed: dict[str, str] = {}
        self.releases = 0
        self.completion_calls = 0
        self.renew_calls = 0
        self.fail_renew = False
        self.renewed = asyncio.Event()
        self.renew_attempted = asyncio.Event()

    async def begin(self, identity: dict[str, str]) -> ExecutionClaim:
        job_id = identity["job_id"]
        if job_id in self.completed:
            return ExecutionClaim(
                job_id=job_id,
                owner="",
                binding_digest="completed",
                result_digest=self.completed[job_id],
            )
        if self.current is not None and self.now < self.expires_at:
            raise JobTaskRetryRequired("job_execution_already_running")
        self.owner_sequence += 1
        claim = ExecutionClaim(
            job_id=job_id,
            owner=f"owner-{self.owner_sequence}",
            binding_digest=f"binding-{self.owner_sequence}",
        )
        self.current = claim
        self.expires_at = self.now + self.ttl_seconds
        return claim

    async def renew(self, claim: ExecutionClaim) -> None:
        self.renew_calls += 1
        self.renew_attempted.set()
        if self.fail_renew:
            raise JobTaskExecutionUncertain("job_execution_renew_failed")
        if self.current != claim or self.now >= self.expires_at:
            raise JobTaskLeaseLost()
        self.expires_at = self.now + self.ttl_seconds
        self.renewed.set()

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        self.completion_calls += 1
        if self.current != claim or self.now >= self.expires_at:
            raise JobTaskLeaseLost()
        self.completed[claim.job_id] = result_digest
        self.current = None

    async def release(self, claim: ExecutionClaim) -> None:
        self.releases += 1
        if self.current == claim:
            self.current = None


async def wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=1.0)


def pending_job_tasks() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith(("plugin-job-handler:", "plugin-job-lease:"))
    ]


@pytest.mark.asyncio
async def test_long_handler_renews_lease_blocks_second_claim_and_stops_watchdog() -> None:
    store = ControlledExecutionStore()
    started = asyncio.Event()
    finish = asyncio.Event()
    cleaned = asyncio.Event()
    seen_keys: list[str] = []

    async def handler(context: Any) -> str:
        seen_keys.append(context.backend_idempotency_key)
        started.set()
        try:
            await finish.wait()
            return RESULT_DIGEST
        finally:
            cleaned.set()

    tasks = make_tasks(handler, executions=store)
    job_id = job_id_for(execution_identity())
    execution = asyncio.create_task(
        tasks.execute(task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs())
    )
    await wait(started)

    store.now = 1.5
    await wait(store.renewed)
    with pytest.raises(JobTaskRetryRequired):
        await store.begin(identity_with_job_id())

    store.renewed.clear()
    store.now = 3.0  # Beyond the original TTL, but not the renewed lease.
    await wait(store.renewed)
    with pytest.raises(JobTaskRetryRequired):
        await store.begin(identity_with_job_id())

    finish.set()
    assert await execution == RESULT_DIGEST
    assert cleaned.is_set()
    assert seen_keys == [job_id]
    assert store.completed[job_id] == RESULT_DIGEST
    renewals = store.renew_calls
    await asyncio.sleep(0.02)
    assert store.renew_calls == renewals

    # Completed dedup neither invokes the handler nor starts another watchdog.
    assert await tasks.execute(
        task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs()
    ) == RESULT_DIGEST
    assert seen_keys == [job_id]
    assert store.renew_calls == renewals
    assert pending_job_tasks() == []


@pytest.mark.asyncio
async def test_sync_blocking_handler_is_renewed_off_loop_and_joined() -> None:
    store = ControlledExecutionStore()
    started = threading.Event()
    finish = threading.Event()
    seen_keys: list[str] = []

    def handler(context: Any) -> str:
        seen_keys.append(context.backend_idempotency_key)
        started.set()
        assert finish.wait(timeout=1.0)
        return RESULT_DIGEST

    job_id = job_id_for(execution_identity())
    tasks = make_tasks(handler, executions=store)
    execution = asyncio.create_task(
        tasks.execute(task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs())
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    store.now = 1.5
    await wait(store.renewed)
    with pytest.raises(JobTaskRetryRequired):
        await store.begin(identity_with_job_id())
    finish.set()
    assert await execution == RESULT_DIGEST
    assert seen_keys == [job_id]
    assert store.completed[job_id] == RESULT_DIGEST
    assert pending_job_tasks() == []


@pytest.mark.asyncio
async def test_renew_failure_is_uncertain_without_release_retry_or_foreign_result() -> None:
    store = ControlledExecutionStore()
    store.fail_renew = True
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(_context: Any) -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return RESULT_DIGEST

    tasks = make_tasks(handler, executions=store)
    job_id = job_id_for(execution_identity())
    execution = asyncio.create_task(
        tasks.execute(task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs())
    )
    await wait(started)
    await wait(store.renew_attempted)
    with pytest.raises(JobTaskExecutionUncertain, match="job_execution_renew_failed"):
        await execution

    assert cleaned.is_set()
    assert store.releases == 0
    assert store.completion_calls == 0
    assert store.completed == {}
    assert pending_job_tasks() == []

    # A different owner may claim only after expiry; the failed first execution
    # still cannot complete or overwrite the new owner's record.
    store.now = 3.0
    second = await store.begin(identity_with_job_id())
    assert second.owner != "owner-1"
    with pytest.raises(JobTaskLeaseLost):
        await store.complete(
            ExecutionClaim(
                job_id=job_id,
                owner="owner-1",
                binding_digest="binding-1",
            ),
            result_digest=RESULT_DIGEST,
        )
    assert store.completed == {}


@pytest.mark.asyncio
async def test_external_cancellation_awaits_handler_and_watchdog_without_release() -> None:
    store = ControlledExecutionStore()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def handler(_context: Any) -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return RESULT_DIGEST

    tasks = make_tasks(handler, executions=store)
    execution = asyncio.create_task(
        tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    )
    await wait(started)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert cleaned.is_set()
    assert store.releases == 0
    assert store.completed == {}
    renewals = store.renew_calls
    await asyncio.sleep(0.02)
    assert store.renew_calls == renewals
    assert pending_job_tasks() == []


@pytest.mark.asyncio
async def test_sync_handler_cancellation_joins_real_thread_without_release() -> None:
    store = ControlledExecutionStore()
    started = threading.Event()
    finish = threading.Event()
    cleaned = threading.Event()

    def handler(_context: Any) -> str:
        started.set()
        try:
            finish.wait(timeout=1.0)
            return RESULT_DIGEST
        finally:
            cleaned.set()

    tasks = make_tasks(handler, executions=store)
    execution = asyncio.create_task(
        tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    execution.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert cleaned.is_set()
    assert store.releases == 0
    assert store.completed == {}
    assert pending_job_tasks() == []


@pytest.mark.asyncio
async def test_handler_error_cleans_watchdog_and_keeps_pre_side_effect_release() -> None:
    store = ControlledExecutionStore()
    cleaned = asyncio.Event()

    async def handler(_context: Any) -> str:
        try:
            raise ValueError("handler failed")
        finally:
            cleaned.set()

    tasks = make_tasks(handler, executions=store)
    with pytest.raises(JobTaskExecutionError, match="job_task_handler_failed"):
        await tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    assert cleaned.is_set()
    assert store.releases == 1
    assert store.completed == {}
    renewals = store.renew_calls
    await asyncio.sleep(0.02)
    assert store.renew_calls == renewals
    assert pending_job_tasks() == []


@pytest.mark.asyncio
async def test_post_validation_renews_until_completion_cas_and_deduplicates() -> None:
    store = ControlledExecutionStore()
    validation_started = asyncio.Event()
    finish_validation = asyncio.Event()
    handler_calls: list[str] = []

    def handler(context: Any) -> str:
        handler_calls.append(context.job_id)
        return RESULT_DIGEST

    tasks = make_tasks(handler, executions=store)
    original_context = tasks._context
    context_calls = 0

    async def blocked_context(spec: Any, *, job_id: str, kwargs: Any) -> Any:
        nonlocal context_calls
        context_calls += 1
        if context_calls == 1:
            return await original_context(spec, job_id=job_id, kwargs=kwargs)
        validation_started.set()
        await finish_validation.wait()
        return await original_context(spec, job_id=job_id, kwargs=kwargs)

    tasks._context = blocked_context
    job_id = job_id_for(execution_identity())
    execution = asyncio.create_task(
        tasks.execute(task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs())
    )
    await wait(validation_started)

    store.now = 1.5
    await wait(store.renewed)
    with pytest.raises(JobTaskRetryRequired):
        await store.begin(identity_with_job_id())

    store.renewed.clear()
    store.now = 3.0  # The post-validation wait exceeds the original lease TTL.
    await wait(store.renewed)
    with pytest.raises(JobTaskRetryRequired):
        await store.begin(identity_with_job_id())

    finish_validation.set()
    assert await execution == RESULT_DIGEST
    assert store.completion_calls == 1
    assert store.completed[job_id] == RESULT_DIGEST
    renewals = store.renew_calls
    await asyncio.sleep(0.02)
    assert store.renew_calls == renewals
    assert pending_job_tasks() == []

    assert await tasks.execute(
        task_name=TASK_NAME, job_id=job_id, kwargs=execution_kwargs()
    ) == RESULT_DIGEST
    assert handler_calls == [job_id]
    assert store.renew_calls == renewals


@pytest.mark.asyncio
async def test_post_validation_renew_failure_cancels_operation_without_success() -> None:
    store = ControlledExecutionStore()
    validation_started = asyncio.Event()
    validation_cleaned = asyncio.Event()

    tasks = make_tasks(lambda _context: RESULT_DIGEST, executions=store)
    original_context = tasks._context
    context_calls = 0

    async def blocked_context(spec: Any, *, job_id: str, kwargs: Any) -> Any:
        nonlocal context_calls
        context_calls += 1
        if context_calls == 1:
            return await original_context(spec, job_id=job_id, kwargs=kwargs)
        validation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            validation_cleaned.set()
        return await original_context(spec, job_id=job_id, kwargs=kwargs)

    tasks._context = blocked_context
    execution = asyncio.create_task(
        tasks.execute(
            task_name=TASK_NAME,
            job_id=job_id_for(execution_identity()),
            kwargs=execution_kwargs(),
        )
    )
    await wait(validation_started)
    store.renew_attempted.clear()
    store.fail_renew = True
    await wait(store.renew_attempted)
    with pytest.raises(JobTaskExecutionUncertain, match="job_execution_renew_failed"):
        await execution

    assert validation_cleaned.is_set()
    assert store.completion_calls == 0
    assert store.completed == {}
    assert store.releases == 0
    assert pending_job_tasks() == []


class NxRedis:
    """Minimal redis.asyncio NX behavior: True on create, None on existing."""

    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.key = ""
        self.set_calls = 0

    async def set(self, key: str, value: str, **_kwargs: Any) -> bool | None:
        self.set_calls += 1
        if self.record is not None:
            return None
        self.key = key
        self.record = json.loads(value)
        return True

    async def get(self, key: str) -> str | None:
        if key != self.key or self.record is None:
            return None
        return json.dumps(self.record)

    async def eval(self, *_args: Any) -> Any:
        raise AssertionError("not used in NX begin test")


@pytest.mark.asyncio
async def test_redis_nx_none_reads_running_conflict_and_completed_dedup() -> None:
    redis = NxRedis()
    store = RedisJobExecutionStore(redis=redis)
    identity = identity_with_job_id()
    await store.begin(identity)
    assert redis.set_calls == 1

    with pytest.raises(JobTaskRetryRequired, match="job_execution_already_running"):
        await store.begin(identity)
    assert redis.set_calls == 2

    assert redis.record is not None
    redis.record["state"] = "completed"
    redis.record["result_digest"] = RESULT_DIGEST
    deduplicated = await store.begin(identity)
    assert deduplicated.is_new is False
    assert deduplicated.result_digest == RESULT_DIGEST
    assert redis.set_calls == 3


class EvalRedis:
    def __init__(self) -> None:
        self.results: list[Any] = []
        self.calls: list[tuple[Any, ...]] = []

    async def get(self, _key: str) -> Any:
        return None

    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def eval(self, *args: Any) -> Any:
        self.calls.append(args)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def redis_claim() -> ExecutionClaim:
    return ExecutionClaim(
        job_id="job_redis",
        owner="owner-1",
        binding_digest="binding-1",
    )


@pytest.mark.asyncio
async def test_redis_renew_and_complete_are_owner_binding_cas() -> None:
    redis = EvalRedis()
    store = RedisJobExecutionStore(
        redis=redis,
        lease_seconds=30,
        renew_interval_seconds=5,
    )
    claim = redis_claim()
    redis.results = [1]
    await store.renew(claim)
    script, numkeys, key, owner, binding, ttl_ms = redis.calls[-1]
    assert "record.state ~= 'running'" in script
    assert "record.owner ~= ARGV[1]" in script
    assert "record.binding_digest ~= ARGV[2]" in script
    assert "pexpire" in script
    assert (numkeys, owner, binding, ttl_ms) == (1, "owner-1", "binding-1", 30000)
    assert key == store._key(claim.job_id)
    assert key != claim.job_id  # Redis keys are derived by the server, not raw input.

    mismatched = replace(claim, owner="other-owner", binding_digest="other-binding")
    redis.results = [0, 0]
    with pytest.raises(JobTaskLeaseLost):
        await store.renew(mismatched)
    with pytest.raises(JobTaskLeaseLost):
        await store.complete(mismatched, result_digest=RESULT_DIGEST)
    completion_script = redis.calls[-1][0]
    assert "record.state ~= 'running'" in completion_script
    assert "record.owner ~= ARGV[1]" in completion_script
    assert "record.binding_digest ~= ARGV[2]" in completion_script

    # A successfully completed lease can never be renewed.
    redis.results = [1, 0]
    await store.complete(claim, result_digest=RESULT_DIGEST)
    with pytest.raises(JobTaskLeaseLost):
        await store.renew(claim)

    # Missing/expired/changed leases use the same deny result.
    redis.results = [0, 0]
    with pytest.raises(JobTaskLeaseLost):
        await store.renew(claim)
    with pytest.raises(JobTaskLeaseLost):
        await store.renew(claim)

    redis.results = [RuntimeError("redis unavailable")]
    with pytest.raises(JobTaskExecutionUncertain, match="job_execution_renew_failed"):
        await store.renew(claim)

    with pytest.raises(JobTaskConfigurationError, match="renew_interval_invalid"):
        RedisJobExecutionStore(redis=redis, lease_seconds=30, renew_interval_seconds=30)


class FakeCelery:
    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {}

    def task(self, **options: Any) -> Any:
        def decorate(function: Any) -> Any:
            self.tasks[options["name"]] = function
            return function

        return decorate


def test_renew_uncertainty_is_not_a_celery_automatic_retry() -> None:
    retry_calls: list[dict[str, Any]] = []

    def retry(**kwargs: Any) -> Exception:
        retry_calls.append(kwargs)
        return RuntimeError("retry scheduled")

    def uncertain(coroutine: Any) -> str:
        coroutine.close()
        raise JobTaskExecutionUncertain("job_execution_renew_failed")

    tasks = make_tasks(async_runner=uncertain)
    app = FakeCelery()
    tasks.register(app)
    bound = SimpleNamespace(
        request=SimpleNamespace(id=job_id_for(execution_identity()), retries=0),
        retry=retry,
    )
    with pytest.raises(JobTaskExecutionUncertain, match="job_execution_renew_failed"):
        app.tasks[TASK_NAME](bound, **execution_kwargs())
    assert retry_calls == []
