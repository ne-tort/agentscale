"""Typed manifest-bound jobs over the Celery/Redis queue boundary."""
from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from prodavan.application.plugins.capabilities.contracts import (
    JobLease,
    JobState,
    JobsCancelRequest,
    JobsCancelResponse,
    JobsGetRequest,
    JobsGetResponse,
    JobsSubmitRequest,
    JobsSubmitResponse,
    empty_digest,
)
from prodavan.application.plugins.jobs.tasks import (
    JobTaskAccessDenied,
    JobTaskConfigurationError,
    JobTaskResolution,
    JobTaskError,
)
from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    usage,
    await_io,
    canonical_digest,
    canonical_json,
    plugin_id,
    request_envelope,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class JobQueue(Protocol):
    async def submit(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]: ...
    async def get(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]: ...
    async def cancel(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]: ...


class JobTaskResolver(Protocol):
    async def resolve(self, request: JobsSubmitRequest) -> JobTaskResolution: ...


class CeleryJobsAdapter(CloseOnce):
    """Expose only server mappings of manifest operations to Celery handlers."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        allowed_tasks: JobTaskResolver,
        timeout_seconds: float = 8.0,
        owns_queue: bool = False,
        owns_tasks: bool = True,
    ) -> None:
        resources = (
            *((queue,) if owns_queue else ()),
            *((allowed_tasks,) if owns_tasks else ()),
        )
        super().__init__(timeout_seconds=timeout_seconds, owned_resources=resources)
        if queue is None or not all(
            callable(getattr(queue, method, None)) for method in ("submit", "get", "cancel")
        ):
            raise CapabilityConfigurationError("celery_job_queue_required")
        if allowed_tasks is None or not callable(getattr(allowed_tasks, "resolve", None)):
            raise CapabilityConfigurationError("manifest_job_task_resolver_required")
        self._queue = queue
        self._allowed_tasks = allowed_tasks

    async def _resolve(self, request: JobsSubmitRequest) -> JobTaskResolution:
        try:
            resolution = await self._allowed_tasks.resolve(request)
        except (JobTaskAccessDenied, JobTaskConfigurationError) as exc:
            # A stale backend or an undeclared operation is a typed denial. A
            # malformed server mapping is never silently converted to a task.
            raise CapabilityAccessDenied(exc.code) from None
        except JobTaskError as exc:
            raise CapabilityBackendError(exc.code) from None
        except Exception:
            raise CapabilityBackendError("job_task_resolution_failed") from None
        if not isinstance(resolution, JobTaskResolution):
            raise CapabilityBackendError("job_task_resolution_invalid")
        return resolution

    @staticmethod
    def _identity(
        request: Any,
        *,
        job_id: str | None = None,
        resolution: JobTaskResolution | None = None,
    ) -> dict[str, str]:
        envelope = request.envelope
        run_id = getattr(request, "run_id", "")
        actual_job_id = job_id or (resolution.job_id if resolution is not None else "")
        if not actual_job_id:
            raise CapabilityAccessDenied("job_id_binding_missing")
        identity = {
            "company_id": envelope.company_id,
            "plugin_id": plugin_id(envelope),
            "plugin_instance_id": envelope.plugin_instance_id,
            "grant_id": envelope.grant_id,
            "artifact_digest": envelope.artifact_digest,
            "run_id": run_id,
            "job_id": actual_job_id,
        }
        if resolution is not None:
            if (
                resolution.company_id != identity["company_id"]
                or resolution.plugin_id != identity["plugin_id"]
                or resolution.plugin_instance_id != identity["plugin_instance_id"]
                or resolution.grant_id != identity["grant_id"]
                or resolution.artifact_digest != identity["artifact_digest"]
                or resolution.run_id != run_id
            ):
                raise CapabilityAccessDenied("job_identity_binding_mismatch")
            identity.update({
                "operation": resolution.operation,
                "task_name": resolution.task_name,
                "backend_digest": resolution.backend_digest,
                "arguments_digest": resolution.arguments_digest,
            })
        else:
            identity["arguments_digest"] = getattr(
                request, "arguments_digest", empty_digest()
            )
        lease_id = getattr(request, "lease_id", None)
        if lease_id is not None:
            identity["lease_id"] = lease_id
        return identity

    @staticmethod
    def _record(raw: Any, identity: Mapping[str, str]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise CapabilityBackendError("job_queue_invalid_record")
        bound_fields = (
            "job_id", "run_id", "company_id", "plugin_id", "plugin_instance_id",
            "grant_id", "artifact_digest",
        )
        for key in bound_fields:
            if raw.get(key) != identity[key]:
                raise CapabilityAccessDenied("job_tenant_or_run_binding_mismatch")
        if "task_name" in identity:
            for key in ("task_name", "operation", "backend_digest", "arguments_digest"):
                if raw.get(key) != identity[key]:
                    raise CapabilityAccessDenied("job_task_or_digest_binding_mismatch")
        try:
            state = JobState(raw.get("state"))
        except ValueError:
            raise CapabilityBackendError("job_queue_invalid_state") from None
        lease_raw = raw.get("lease")
        lease: JobLease | None = None
        if lease_raw is not None:
            if not isinstance(lease_raw, Mapping):
                raise CapabilityBackendError("job_lease_invalid")
            try:
                lease = JobLease(
                    lease_id=str(lease_raw["lease_id"]),
                    lease_owner=str(lease_raw["lease_owner"]),
                    expires_at_epoch=int(lease_raw["expires_at_epoch"]),
                )
            except (KeyError, TypeError, ValueError):
                raise CapabilityBackendError("job_lease_invalid") from None
        result_digest = raw.get("result_digest", empty_digest())
        if not isinstance(result_digest, str) or _DIGEST.fullmatch(result_digest) is None:
            raise CapabilityBackendError("job_result_digest_invalid")
        return {
            "job_id": identity["job_id"],
            "run_id": identity["run_id"],
            "state": state,
            "lease": lease,
            "result_digest": result_digest,
        }

    async def _call(self, method: str, identity: Mapping[str, str]) -> dict[str, Any]:
        self._ensure_open()
        raw = await await_io(
            getattr(self._queue, method)(identity=identity),
            timeout=self._timeout_seconds,
            code=f"job_queue_{method}_failed",
        )
        return self._record(raw, identity)

    def quota_usage(self, request: Any) -> Any:
        if isinstance(request, JobsSubmitRequest):
            async def submit_usage() -> Any:
                request_envelope(request, JobsSubmitRequest)
                resolution = await self._resolve(request)
                identity = self._identity(request, resolution=resolution)
                return usage(rows=1, keys=1, bytes=len(canonical_json(identity)))
            return submit_usage()
        if not isinstance(request, (JobsGetRequest, JobsCancelRequest)):
            raise CapabilityAccessDenied("typed_request_required")
        request_envelope(request, type(request))
        identity = self._identity(request, job_id=request.job_id)
        return usage(rows=1, keys=1, bytes=len(canonical_json(identity)))

    async def submit(self, request: JobsSubmitRequest) -> JobsSubmitResponse:
        envelope = request_envelope(request, JobsSubmitRequest)
        if envelope.run_id is not None and envelope.run_id != request.run_id:
            raise CapabilityAccessDenied("job_run_id_envelope_mismatch")
        resolution = await self._resolve(request)
        identity = self._identity(request, resolution=resolution)
        record = await self._call("submit", identity)
        return JobsSubmitResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({**identity, "state": record["state"].value}),
            job_id=identity["job_id"],
            run_id=request.run_id,
            state=record["state"],
            lease=record["lease"],
        )

    async def get(self, request: JobsGetRequest) -> JobsGetResponse:
        envelope = request_envelope(request, JobsGetRequest)
        if envelope.run_id is not None and envelope.run_id != request.run_id:
            raise CapabilityAccessDenied("job_run_id_envelope_mismatch")
        identity = self._identity(request, job_id=request.job_id)
        record = await self._call("get", identity)
        return JobsGetResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "job_id": identity["job_id"],
                "run_id": request.run_id,
                "state": record["state"].value,
                "result_digest": record["result_digest"],
            }),
            job_id=identity["job_id"],
            run_id=request.run_id,
            state=record["state"],
            result_digest=record["result_digest"],
            lease=record["lease"],
        )

    async def cancel(self, request: JobsCancelRequest) -> JobsCancelResponse:
        envelope = request_envelope(request, JobsCancelRequest)
        if envelope.run_id is not None and envelope.run_id != request.run_id:
            raise CapabilityAccessDenied("job_run_id_envelope_mismatch")
        identity = self._identity(request, job_id=request.job_id)
        record = await self._call("cancel", identity)
        return JobsCancelResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "job_id": identity["job_id"],
                "state": record["state"].value,
            }),
            job_id=identity["job_id"],
            state=record["state"],
        )


__all__ = ["CeleryJobsAdapter", "JobQueue", "JobTaskResolver"]
