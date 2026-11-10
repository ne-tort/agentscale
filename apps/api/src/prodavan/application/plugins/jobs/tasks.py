"""Server-owned job allowlist and digest-fenced Celery handlers.

A plugin submits a manifest operation reference, never a Celery task name. The
server maps that reference to a concrete handler and backend digest. The same
check is repeated in the worker immediately before the handler and before its
result is committed. No task result is manufactured by the wrapper.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import hashlib
import inspect
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TASK_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,255}$")
_OPERATION = re.compile(
    r"^[a-z][a-z0-9_-]{1,63}(?:\.[a-z][a-z0-9_-]{1,63})?@[0-9]+\.[0-9]+:[a-z][a-z0-9_.-]{0,63}$"
)
_REQUIRED_KWARGS = frozenset({
    "company_id", "plugin_id", "plugin_instance_id", "grant_id", "run_id",
    "arguments_digest",
})


class JobTaskError(RuntimeError):
    """Safe typed failure; code never contains backend exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JobTaskConfigurationError(JobTaskError):
    pass


class JobTaskAccessDenied(JobTaskError):
    pass


class JobTaskBackendError(JobTaskError):
    pass


class JobTaskExecutionError(JobTaskError):
    pass


class JobTaskExecutionUncertain(JobTaskError):
    """Side effects are uncertain: never release for retry or report success."""

    def __init__(self, code: str = "job_execution_outcome_uncertain") -> None:
        super().__init__(code)


class JobTaskLeaseLost(JobTaskExecutionUncertain):
    def __init__(self, code: str = "job_execution_lease_lost") -> None:
        super().__init__(code)


class JobTaskRetryRequired(JobTaskError):
    """Another worker owns the execution lease; this attempt did no work."""


class StaleJobTaskDigest(JobTaskError):
    def __init__(self) -> None:
        super().__init__("job_backend_digest_stale")


@dataclass(frozen=True, slots=True)
class JobTaskSpec:
    operation: str
    task_name: str
    backend_digest: str
    handler: Callable[["JobTaskContext"], str | Awaitable[str]]

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or _OPERATION.fullmatch(self.operation) is None:
            raise JobTaskConfigurationError("job_operation_reference_invalid")
        if not isinstance(self.task_name, str) or _TASK_NAME.fullmatch(self.task_name) is None:
            raise JobTaskConfigurationError("job_celery_task_name_invalid")
        _require_digest(self.backend_digest, "job_backend_digest_invalid")
        if not callable(self.handler):
            raise JobTaskConfigurationError("job_task_handler_required")


@dataclass(frozen=True, slots=True)
class JobTaskResolution:
    spec: JobTaskSpec
    job_id: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    grant_id: str
    run_id: str
    arguments_digest: str
    artifact_digest: str

    @property
    def operation(self) -> str:
        return self.spec.operation

    @property
    def task_name(self) -> str:
        return self.spec.task_name

    @property
    def backend_digest(self) -> str:
        return self.spec.backend_digest


@dataclass(frozen=True, slots=True)
class JobTaskContext:
    job_id: str
    operation: str
    task_name: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    grant_id: str
    run_id: str
    arguments_digest: str
    artifact_digest: str
    backend_digest: str

    @property
    def backend_idempotency_key(self) -> str:
        """Canonical stable key for backend idempotency across worker retries."""

        return self.job_id

    def identity(self) -> dict[str, str]:
        return {
            name: str(getattr(self, name))
            for name in (
                "job_id", "operation", "task_name", "company_id", "plugin_id",
                "plugin_instance_id", "grant_id", "run_id", "arguments_digest",
                "artifact_digest", "backend_digest",
            )
        }


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    job_id: str
    owner: str
    binding_digest: str
    result_digest: str | None = None

    @property
    def is_new(self) -> bool:
        return self.result_digest is None


class JobExecutionStore(Protocol):
    @property
    def renew_interval_seconds(self) -> float: ...

    async def begin(self, identity: Mapping[str, str]) -> ExecutionClaim: ...
    async def renew(self, claim: ExecutionClaim) -> None: ...
    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None: ...
    async def release(self, claim: ExecutionClaim) -> None: ...


def _require_digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise JobTaskConfigurationError(code)
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise JobTaskBackendError("job_identity_not_canonicalizable") from None


def _binding_digest(identity: Mapping[str, str]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(dict(identity))).hexdigest()


def job_operation_key(capability_id: str, version: str, operation: str) -> str:
    """Canonical manifest reference capability@major.minor:operation."""

    candidate = f"{capability_id}@{version}:{operation}"
    if _OPERATION.fullmatch(candidate) is None:
        raise JobTaskConfigurationError("job_operation_reference_invalid")
    return candidate


def job_id_for(identity: Mapping[str, str]) -> str:
    """Return the deterministic backend idempotency key shared by every retry."""

    required = (
        "company_id", "plugin_id", "plugin_instance_id", "grant_id", "operation",
        "task_name", "run_id", "arguments_digest", "artifact_digest", "backend_digest",
    )
    if any(not isinstance(identity.get(name), str) or not identity.get(name) for name in required):
        raise JobTaskAccessDenied("job_identity_incomplete")
    return "job_" + _binding_digest({name: identity[name] for name in required})[7:]


class RedisJobExecutionStore:
    """Atomic execution lease plus completed-result dedup in Redis.

    A running lease is never reported as a successful result. Completed dedup
    returns only the exact digest previously produced by the real handler.
    """

    def __init__(
        self,
        *,
        redis: Any,
        lease_seconds: int = 300,
        retention_seconds: int = 604800,
        renew_interval_seconds: float | None = None,
    ) -> None:
        if redis is None or not all(
            callable(getattr(redis, method, None)) for method in ("get", "set", "eval")
        ):
            raise JobTaskConfigurationError("job_execution_redis_required")
        if not 30 <= lease_seconds <= 3600 or not 60 <= retention_seconds <= 2592000:
            raise JobTaskConfigurationError("job_execution_ttl_invalid")
        try:
            interval = (
                lease_seconds / 3
                if renew_interval_seconds is None
                else float(renew_interval_seconds)
            )
        except (TypeError, ValueError):
            raise JobTaskConfigurationError("job_execution_renew_interval_invalid") from None
        if (
            isinstance(renew_interval_seconds, bool)
            or not math.isfinite(interval)
            or not 0 < interval < lease_seconds
        ):
            raise JobTaskConfigurationError("job_execution_renew_interval_invalid")
        self._redis = redis
        self._lease_seconds = lease_seconds
        self._retention_seconds = retention_seconds
        self._renew_interval_seconds = interval

    @property
    def renew_interval_seconds(self) -> float:
        return self._renew_interval_seconds

    @staticmethod
    def _key(job_id: str) -> str:
        if not isinstance(job_id, str) or not job_id.startswith("job_"):
            raise JobTaskAccessDenied("job_id_invalid")
        return "plugin:jobs:execution:" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except (TypeError, ValueError):
            value = None
        if not isinstance(value, dict):
            raise JobTaskBackendError("job_execution_record_invalid")
        return value

    async def begin(self, identity: Mapping[str, str]) -> ExecutionClaim:
        job_id = identity.get("job_id", "")
        key = self._key(job_id)
        binding = _binding_digest(identity)
        owner = uuid.uuid4().hex
        running = _canonical_bytes({
            "state": "running", "owner": owner, "binding_digest": binding,
        }).decode("utf-8")
        try:
            created = await self._redis.set(
                key, running, nx=True, ex=self._lease_seconds,
            )
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskBackendError("job_execution_claim_failed") from None
        # redis.asyncio returns None for a normal NX miss. False/0 is accepted
        # for compatible clients; only other response types are unconfirmed.
        if created is not None and not isinstance(created, (bool, int)):
            raise JobTaskBackendError("job_execution_claim_unconfirmed")
        if created:
            return ExecutionClaim(job_id=job_id, owner=owner, binding_digest=binding)
        try:
            raw = await self._redis.get(key)
        except Exception:
            raise JobTaskBackendError("job_execution_read_failed") from None
        if raw is None:
            raise JobTaskRetryRequired("job_execution_claim_raced")
        record = self._decode(raw)
        if record.get("binding_digest") != binding:
            raise JobTaskAccessDenied("job_execution_binding_mismatch")
        if record.get("state") == "completed":
            result = record.get("result_digest")
            if not isinstance(result, str) or _DIGEST.fullmatch(result) is None:
                raise JobTaskBackendError("job_execution_result_invalid")
            return ExecutionClaim(
                job_id=job_id, owner="", binding_digest=binding, result_digest=result,
            )
        if record.get("state") != "running":
            raise JobTaskBackendError("job_execution_state_invalid")
        raise JobTaskRetryRequired("job_execution_already_running")

    async def renew(self, claim: ExecutionClaim) -> None:
        """Atomically extend only this owner's matching running lease."""

        if not claim.is_new or not claim.owner:
            raise JobTaskExecutionUncertain("job_execution_renew_invalid")
        script = """
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local record = cjson.decode(raw)
if record.state ~= 'running' or record.owner ~= ARGV[1] or record.binding_digest ~= ARGV[2] then return 0 end
return redis.call('pexpire', KEYS[1], tonumber(ARGV[3]))
"""
        try:
            result = await self._redis.eval(
                script,
                1,
                self._key(claim.job_id),
                claim.owner,
                claim.binding_digest,
                int(self._lease_seconds * 1000),
            )
        except JobTaskExecutionUncertain:
            raise
        except Exception:
            raise JobTaskExecutionUncertain("job_execution_renew_failed") from None
        if result != 1:
            raise JobTaskLeaseLost()

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        if not claim.is_new or not claim.owner or _DIGEST.fullmatch(result_digest) is None:
            raise JobTaskBackendError("job_execution_completion_invalid")
        script = """
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local record = cjson.decode(raw)
if record.state ~= 'running' or record.owner ~= ARGV[1] or record.binding_digest ~= ARGV[2] then return 0 end
record.state = 'completed'
record.result_digest = ARGV[3]
redis.call('set', KEYS[1], cjson.encode(record), 'EX', tonumber(ARGV[4]))
return 1
"""
        try:
            result = await self._redis.eval(
                script, 1, self._key(claim.job_id),
                claim.owner, claim.binding_digest, result_digest, self._retention_seconds,
            )
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskExecutionUncertain("job_execution_completion_failed") from None
        if result != 1:
            raise JobTaskLeaseLost()

    async def release(self, claim: ExecutionClaim) -> None:
        if not claim.is_new or not claim.owner:
            return
        script = """
local raw = redis.call('get', KEYS[1])
if not raw then return 1 end
local record = cjson.decode(raw)
if record.state == 'running' and record.owner == ARGV[1] and record.binding_digest == ARGV[2] then
  return redis.call('del', KEYS[1])
end
return 0
"""
        try:
            result = await self._redis.eval(
                script, 1, self._key(claim.job_id), claim.owner, claim.binding_digest,
            )
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskBackendError("job_execution_release_failed") from None
        if result != 1:
            raise JobTaskBackendError("job_execution_release_fence_failed")


class ManifestJobTasks:
    """Resolve manifest operations and execute their digest-fenced handlers."""

    def __init__(
        self,
        *,
        tasks: Mapping[str, JobTaskSpec | Mapping[str, Any]],
        grant_store: Any,
        artifact_registry: Any,
        execution_store: JobExecutionStore,
        async_runner: Callable[[Awaitable[str]], str] | None = None,
    ) -> None:
        if not isinstance(tasks, Mapping) or not tasks:
            raise JobTaskConfigurationError("job_task_allowlist_required")
        specs: dict[str, JobTaskSpec] = {}
        for operation, raw in tasks.items():
            if not isinstance(operation, str):
                raise JobTaskConfigurationError("job_operation_reference_invalid")
            if isinstance(raw, JobTaskSpec):
                spec = raw
            elif isinstance(raw, Mapping):
                if set(raw) != {"task_name", "backend_digest", "handler"}:
                    raise JobTaskConfigurationError("job_task_mapping_invalid")
                spec = JobTaskSpec(operation=operation, **dict(raw))
            else:
                raise JobTaskConfigurationError("job_task_mapping_invalid")
            if spec.operation != operation:
                raise JobTaskConfigurationError("job_task_operation_key_mismatch")
            specs[operation] = spec
        names = [spec.task_name for spec in specs.values()]
        if len(set(names)) != len(names):
            raise JobTaskConfigurationError("job_celery_task_name_duplicate")
        if not callable(getattr(grant_store, "get_by_grant_id", None)):
            raise JobTaskConfigurationError("job_grant_store_required")
        self._artifact_reader = self._select_artifact_reader(artifact_registry)
        if not all(
            callable(getattr(execution_store, method, None))
            for method in ("begin", "renew", "complete", "release")
        ):
            raise JobTaskConfigurationError("job_execution_store_required")
        interval = getattr(execution_store, "renew_interval_seconds", None)
        if (
            not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not 0 < interval
            or not math.isfinite(interval)
        ):
            raise JobTaskConfigurationError("job_execution_renew_interval_invalid")
        self._renew_interval_seconds = float(interval)
        if async_runner is not None and not callable(async_runner):
            raise JobTaskConfigurationError("job_async_runner_invalid")
        self._specs = specs
        self._by_task_name = {spec.task_name: spec for spec in specs.values()}
        self._grants = grant_store
        self._execution = execution_store
        self._run_async = async_runner
        self._registered: dict[int, tuple[Any, str]] = {}
        self._closed = False

    @staticmethod
    def _select_artifact_reader(registry: Any) -> Callable[[str], Awaitable[Any]]:
        candidates = [registry, getattr(registry, "_store", None)]
        for candidate in candidates:
            if candidate is None:
                continue
            for name in ("by_digest_async", "by_digest"):
                method = getattr(candidate, name, None)
                if callable(method):
                    async def read(digest: str, fn: Any = method) -> Any:
                        value = fn(digest)
                        return await value if inspect.isawaitable(value) else value
                    return read
        raise JobTaskConfigurationError("job_artifact_registry_required")

    @property
    def specs(self) -> tuple[JobTaskSpec, ...]:
        return tuple(self._specs.values())

    async def _grant(self, grant_id: str) -> Any:
        try:
            record = await self._grants.get_by_grant_id(grant_id=grant_id)
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskBackendError("job_grant_lookup_failed") from None
        if (
            record is None
            or not bool(getattr(record, "is_active", False))
            or getattr(record, "revoked_at", None) is not None
        ):
            raise JobTaskAccessDenied("job_grant_not_active")
        return record

    async def _artifact(self, digest: str) -> Any:
        try:
            record = await self._artifact_reader(digest)
        except JobTaskError:
            raise
        except Exception:
            raise JobTaskBackendError("job_artifact_lookup_failed") from None
        if record is None:
            raise JobTaskAccessDenied("job_artifact_not_found")
        return record

    @staticmethod
    def _declare_operation(*, grant: Any, artifact: Any, spec: JobTaskSpec) -> None:
        if (
            getattr(artifact, "digest", None) != grant.artifact_digest
            or getattr(artifact, "plugin_id", None) != grant.plugin_id
            or getattr(artifact, "version", None) != grant.manifest_version
            or getattr(artifact, "published", False) is not True
            or getattr(artifact, "verification", None) is None
        ):
            raise JobTaskAccessDenied("job_artifact_binding_invalid")
        publication = getattr(artifact, "publication", None)
        if getattr(publication, "backend_digest", None) != spec.backend_digest:
            raise StaleJobTaskDigest()
        manifest = getattr(artifact, "manifest", None)
        raw_operations = manifest.get("rpc_operations") if isinstance(manifest, Mapping) else None
        if not isinstance(raw_operations, list):
            raise JobTaskAccessDenied("job_manifest_operations_missing")
        declarations: set[str] = set()
        for raw in raw_operations:
            if not isinstance(raw, Mapping):
                continue
            capability = raw.get("capability")
            operation = raw.get("operation")
            if not isinstance(capability, Mapping) or not isinstance(operation, str):
                continue
            try:
                key = job_operation_key(
                    str(capability.get("id") or ""),
                    str(capability.get("version") or ""),
                    operation,
                )
            except JobTaskConfigurationError:
                continue
            if key in declarations:
                raise JobTaskBackendError("job_manifest_operations_ambiguous")
            declarations.add(key)
        if spec.operation not in declarations:
            raise JobTaskAccessDenied("job_operation_not_declared")

    async def resolve(self, request: Any) -> JobTaskResolution:
        envelope = getattr(request, "envelope", None)
        operation = getattr(request, "task_name", None)
        spec = self._specs.get(operation) if isinstance(operation, str) else None
        if envelope is None or spec is None:
            raise JobTaskAccessDenied("job_task_not_allowed")
        grant = await self._grant(str(getattr(envelope, "grant_id", "")))
        parts = str(getattr(envelope, "subject_id", "")).split(":")
        expected_subject = f"plugin:{grant.plugin_id}:{grant.company_id}:{grant.plugin_instance_id}"
        if (
            grant.grant_id != getattr(envelope, "grant_id", None)
            or grant.company_id != getattr(envelope, "company_id", None)
            or grant.plugin_instance_id != getattr(envelope, "plugin_instance_id", None)
            or grant.artifact_digest != getattr(envelope, "artifact_digest", None)
            or len(parts) != 4
            or getattr(envelope, "subject_id", None) != expected_subject
        ):
            raise JobTaskAccessDenied("job_identity_binding_mismatch")
        artifact = await self._artifact(grant.artifact_digest)
        self._declare_operation(grant=grant, artifact=artifact, spec=spec)
        identity = {
            "company_id": grant.company_id,
            "plugin_id": grant.plugin_id,
            "plugin_instance_id": grant.plugin_instance_id,
            "grant_id": grant.grant_id,
            "operation": spec.operation,
            "task_name": spec.task_name,
            "run_id": request.run_id,
            "arguments_digest": request.arguments_digest,
            "artifact_digest": grant.artifact_digest,
            "backend_digest": spec.backend_digest,
        }
        return JobTaskResolution(
            spec=spec,
            job_id=job_id_for(identity),
            company_id=grant.company_id,
            plugin_id=grant.plugin_id,
            plugin_instance_id=grant.plugin_instance_id,
            grant_id=grant.grant_id,
            run_id=request.run_id,
            arguments_digest=request.arguments_digest,
            artifact_digest=grant.artifact_digest,
        )

    async def _context(self, spec: JobTaskSpec, *, job_id: str, kwargs: Mapping[str, Any]) -> JobTaskContext:
        if set(kwargs) != _REQUIRED_KWARGS:
            raise JobTaskAccessDenied("job_task_kwargs_invalid")
        values: dict[str, str] = {}
        for name in _REQUIRED_KWARGS:
            value = kwargs.get(name)
            if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
                raise JobTaskAccessDenied("job_task_kwargs_invalid")
            values[name] = value
        _require_digest(values["arguments_digest"], "job_arguments_digest_invalid")
        grant = await self._grant(values["grant_id"])
        expected = {
            "company_id": grant.company_id,
            "plugin_id": grant.plugin_id,
            "plugin_instance_id": grant.plugin_instance_id,
            "grant_id": grant.grant_id,
        }
        if any(values[name] != expected[name] for name in expected):
            raise JobTaskAccessDenied("job_identity_binding_mismatch")
        artifact = await self._artifact(grant.artifact_digest)
        self._declare_operation(grant=grant, artifact=artifact, spec=spec)
        identity = {
            **values,
            "operation": spec.operation,
            "task_name": spec.task_name,
            "artifact_digest": grant.artifact_digest,
            "backend_digest": spec.backend_digest,
        }
        if not isinstance(job_id, str) or job_id_for(identity) != job_id:
            raise JobTaskAccessDenied("job_id_binding_mismatch")
        return JobTaskContext(job_id=job_id, **identity)

    async def _watch_lease(self, claim: ExecutionClaim) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval_seconds)
                await self._execution.renew(claim)
        except asyncio.CancelledError:
            raise
        except JobTaskExecutionUncertain:
            raise
        except Exception:
            raise JobTaskExecutionUncertain("job_execution_renew_failed") from None

    @staticmethod
    async def _cancel_and_wait(tasks: tuple[asyncio.Task[Any], ...]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _call_sync_handler(handler: Callable[[JobTaskContext], Any], context: JobTaskContext) -> Any:
        """Run blocking work off-loop and join it even when cancelled."""

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="plugin-job-sync-handler"
        )
        call_context = contextvars.copy_context()
        future = executor.submit(call_context.run, handler, context)
        try:
            return await asyncio.wrap_future(future)
        finally:
            try:
                if not future.done():
                    # A thread cannot be killed safely. Cancellation waits for the
                    # real callable instead of leaking side effects in the background.
                    await asyncio.to_thread(future.result)
            finally:
                executor.shutdown(wait=True)

    async def _invoke_handler(self, spec: JobTaskSpec, context: JobTaskContext) -> Any:
        is_async = inspect.iscoroutinefunction(spec.handler) or inspect.iscoroutinefunction(
            getattr(spec.handler, "__call__", None)
        )
        if is_async:
            produced = spec.handler(context)
        else:
            produced = await self._call_sync_handler(spec.handler, context)
        if inspect.isawaitable(produced):
            return await produced
        return produced

    async def _run_with_lease(
        self,
        claim: ExecutionClaim,
        operation: Callable[[], Awaitable[str]],
    ) -> str:
        operation_task = asyncio.create_task(
            operation(), name=f"plugin-job-handler:{claim.job_id}"
        )
        watchdog_task = asyncio.create_task(
            self._watch_lease(claim), name=f"plugin-job-lease:{claim.job_id}"
        )
        tasks = (operation_task, watchdog_task)
        try:
            _done, _pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if watchdog_task in _done:
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    raise
                except JobTaskExecutionUncertain:
                    raise
                except Exception:
                    raise JobTaskExecutionUncertain("job_execution_renew_failed") from None
                raise JobTaskExecutionUncertain("job_execution_watchdog_stopped")
            return await operation_task
        finally:
            # The operation includes post-validation and the final completion CAS.
            # Only its terminal outcome stops renewal; both tasks are always joined.
            await self._cancel_and_wait(tasks)

    async def execute(self, *, task_name: str, job_id: str, kwargs: Mapping[str, Any]) -> str:
        if self._closed:
            raise JobTaskExecutionError("job_task_registry_closed")
        spec = self._by_task_name.get(task_name)
        if spec is None:
            raise JobTaskAccessDenied("job_task_not_allowed")
        context = await self._context(spec, job_id=job_id, kwargs=kwargs)
        claim = await self._execution.begin(context.identity())
        if not claim.is_new:
            return str(claim.result_digest)
        post_validation_started = False

        async def perform() -> str:
            nonlocal post_validation_started
            produced = await self._invoke_handler(spec, context)
            if not isinstance(produced, str) or _DIGEST.fullmatch(produced) is None:
                raise JobTaskExecutionError("job_task_result_digest_required")
            post_validation_started = True
            # Renewal remains active through this async revalidation and the final
            # CAS. A mismatch never releases for a side-effecting automatic retry.
            await self._context(spec, job_id=job_id, kwargs=kwargs)
            await self._execution.complete(claim, result_digest=produced)
            return produced

        try:
            return await self._run_with_lease(claim, perform)
        except JobTaskExecutionUncertain:
            # The handler may have caused side effects. Never release for retry or
            # let another result overwrite this execution's uncertain outcome.
            raise
        except asyncio.CancelledError:
            # Cancellation is also uncertain; watchdog/operation cleanup has already
            # completed, but the running lease is deliberately not released.
            raise
        except JobTaskError:
            if not post_validation_started:
                await self._execution.release(claim)
            raise
        except Exception:
            if post_validation_started:
                raise
            await self._execution.release(claim)
            raise JobTaskExecutionError("job_task_handler_failed") from None

    def register(self, celery_app: Any) -> None:
        """Register each concrete task once on the supplied Celery application.

        Existing foreign task names are conflicts, never overwritten. The
        injected runner must use the persistent worker loop; this module never
        calls asyncio.run or creates a loop per request.
        """

        if self._closed:
            raise JobTaskConfigurationError("job_task_registry_closed")
        if self._run_async is None:
            raise JobTaskConfigurationError("job_async_runner_required")
        if celery_app is None or not callable(getattr(celery_app, "task", None)):
            raise JobTaskConfigurationError("job_celery_app_required")
        fingerprint = _binding_digest({
            operation: {
                "task_name": spec.task_name, "backend_digest": spec.backend_digest,
            }
            for operation, spec in self._specs.items()
        })
        previous = self._registered.get(id(celery_app))
        if previous is not None:
            if previous[0] is not celery_app or previous[1] != fingerprint:
                raise JobTaskConfigurationError("job_celery_registration_conflict")
            return
        registry = getattr(celery_app, "tasks", None)
        if registry is None:
            raise JobTaskConfigurationError("job_celery_task_registry_required")
        # Preflight the complete allowlist before mutating the Celery registry:
        # a conflict on a later item must not leave a partially registered set.
        for spec in self._specs.values():
            existing = registry.get(spec.task_name)
            if existing is not None and getattr(existing, "_plugin_job_registry", None) is not self:
                raise JobTaskConfigurationError("job_celery_task_name_conflict")

        for spec in self._specs.values():
            def make_invoke(bound_spec: JobTaskSpec) -> Callable[..., str]:
                def invoke(bound_task: Any, **kwargs: Any) -> str:
                    request = getattr(bound_task, "request", None)
                    job_id = getattr(request, "id", "")
                    coroutine = self.execute(
                        task_name=bound_spec.task_name, job_id=job_id, kwargs=kwargs,
                    )
                    try:
                        return self._run_async(coroutine)  # type: ignore[misc]
                    except JobTaskRetryRequired as exc:
                        # Only a pre-side-effect lease conflict is automatically
                        # retried. Stale identities and uncertain handler outcomes
                        # stay typed failures.
                        retry = getattr(bound_task, "retry", None)
                        if not callable(retry):
                            raise
                        attempts = int(getattr(request, "retries", 0) or 0)
                        raise retry(
                            exc=exc,
                            countdown=min(30.0, 0.1 * (2 ** min(attempts, 8))),
                            max_retries=2,
                        ) from exc
                return invoke

            decorator = celery_app.task(
                name=spec.task_name,
                # Dynamic registries belong to this exact Celery app. Shared
                # tasks leak into apps created later and cause foreign-world
                # name conflicts without providing worker discovery here.
                shared=False,
                bind=True,
                acks_late=True,
                reject_on_worker_lost=True,
                max_retries=2,
            )
            registered = decorator(make_invoke(spec))
            registered._plugin_job_registry = self
            registered._plugin_job_operation = spec.operation
        self._registered[id(celery_app)] = (celery_app, fingerprint)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._registered.clear()


__all__ = [
    "ExecutionClaim",
    "JobExecutionStore",
    "JobTaskAccessDenied",
    "JobTaskBackendError",
    "JobTaskConfigurationError",
    "JobTaskContext",
    "JobTaskError",
    "JobTaskExecutionError",
    "JobTaskExecutionUncertain",
    "JobTaskLeaseLost",
    "JobTaskResolution",
    "JobTaskRetryRequired",
    "JobTaskSpec",
    "ManifestJobTasks",
    "RedisJobExecutionStore",
    "StaleJobTaskDigest",
    "job_id_for",
    "job_operation_key",
]
