"""Shared fakes for manifest-bound job tests; never used by production wiring."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    JobsSubmitRequest,
)
from prodavan.application.plugins.jobs.tasks import (
    ExecutionClaim,
    JobTaskRetryRequired,
    JobTaskSpec,
    ManifestJobTasks,
)

ARTIFACT_DIGEST = "sha256:" + "a" * 64
BACKEND_DIGEST = "sha256:" + "b" * 64
ARGUMENTS_DIGEST = "sha256:" + "c" * 64
RESULT_DIGEST = "sha256:" + "d" * 64
OTHER_DIGEST = "sha256:" + "e" * 64
OPERATION = "hello.jobs@1.0:refresh"
TASK_NAME = "plugin.safe.task"


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-job-1",
        company_id="co_123",
        subject_id="plugin:mod_x:co_123:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=ARTIFACT_DIGEST,
        grant_id="grant_01",
    )


def request(*, run_id: str = "run1", operation: str = OPERATION) -> JobsSubmitRequest:
    return JobsSubmitRequest(
        task_name=operation,
        run_id=run_id,
        arguments_digest=ARGUMENTS_DIGEST,
        envelope=envelope(),
    )


def grant() -> Any:
    return SimpleNamespace(
        grant_id="grant_01",
        company_id="co_123",
        plugin_id="mod_x",
        plugin_instance_id="inst_abc",
        manifest_version="1.0.0",
        artifact_digest=ARTIFACT_DIGEST,
        is_active=True,
    )


class GrantStore:
    def __init__(self, record: Any = None) -> None:
        self.record = record or grant()

    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        return self.record if grant_id == self.record.grant_id else None

    async def get(self, *, company_id: str, plugin_id: str) -> Any:
        if (company_id, plugin_id) != (self.record.company_id, self.record.plugin_id):
            return None
        return self.record


def artifact(*, backend_digest: str = BACKEND_DIGEST, operations: list[Any] | None = None) -> Any:
    if operations is None:
        operations = [{
            "capability": {"id": "hello.jobs", "version": "1.0"},
            "operation": "refresh",
            "http_method": "POST",
            "backend_path": "/api/jobs/refresh",
            "public": False,
        }]
    return SimpleNamespace(
        plugin_id="mod_x",
        version="1.0.0",
        digest=ARTIFACT_DIGEST,
        published=True,
        verification=object(),
        publication=SimpleNamespace(backend_digest=backend_digest),
        manifest={"rpc_operations": operations},
    )


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
        self.renewals = 0

    async def begin(self, identity: dict[str, str]) -> ExecutionClaim:
        job_id = identity["job_id"]
        if job_id in self.completed:
            return ExecutionClaim(
                job_id=job_id,
                owner="",
                binding_digest="sha256:" + "0" * 64,
                result_digest=self.completed[job_id],
            )
        if job_id in self.records:
            raise JobTaskRetryRequired("job_execution_already_running")
        claim = ExecutionClaim(
            job_id=job_id,
            owner="owner-1",
            binding_digest="sha256:" + "0" * 64,
        )
        self.records[job_id] = claim
        return claim

    async def renew(self, claim: ExecutionClaim) -> None:
        if self.records.get(claim.job_id) != claim:
            from prodavan.application.plugins.jobs.tasks import JobTaskLeaseLost

            raise JobTaskLeaseLost()
        self.renewals += 1

    async def complete(self, claim: ExecutionClaim, *, result_digest: str) -> None:
        self.completed[claim.job_id] = result_digest
        self.records.pop(claim.job_id, None)

    async def release(self, claim: ExecutionClaim) -> None:
        self.releases += 1
        self.records.pop(claim.job_id, None)


def make_tasks(
    handler: Any = None,
    *,
    grants: Any = None,
    artifacts: Any = None,
    executions: Any = None,
    async_runner: Any = None,
) -> ManifestJobTasks:
    if handler is None:
        handler = lambda context: RESULT_DIGEST
    return ManifestJobTasks(
        tasks={OPERATION: JobTaskSpec(
            operation=OPERATION,
            task_name=TASK_NAME,
            backend_digest=BACKEND_DIGEST,
            handler=handler,
        )},
        grant_store=grants or GrantStore(),
        artifact_registry=artifacts or ArtifactRegistry(),
        execution_store=executions or ExecutionStore(),
        async_runner=async_runner,
    )


__all__ = [
    "ARGUMENTS_DIGEST",
    "ARTIFACT_DIGEST",
    "ArtifactRegistry",
    "BACKEND_DIGEST",
    "ExecutionStore",
    "GrantStore",
    "OPERATION",
    "OTHER_DIGEST",
    "RESULT_DIGEST",
    "TASK_NAME",
    "artifact",
    "envelope",
    "grant",
    "make_tasks",
    "request",
]
