"""Typed pod actions through a server-side scoped executor, never plugin K8s credentials."""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from prodavan.application.plugins.capabilities.contracts import (
    PodsInvokeRequest,
    PodsInvokeResponse,
)
from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    usage,
    await_io,
    canonical_digest,
    plugin_id,
    request_envelope,
    require_safe_text,
)


class PodActionExecutor(Protocol):
    async def execute(self, *, identity: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ScopedPodsAdapter(CloseOnce):
    def __init__(
        self,
        *,
        executor: PodActionExecutor,
        result_bytes_reservation: int = 1024 * 1024,
        timeout_seconds: float = 15.0,
        owns_executor: bool = True,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(executor,) if owns_executor else (),
        )
        if executor is None or not callable(getattr(executor, "execute", None)):
            raise CapabilityConfigurationError("scoped_pod_action_executor_required")
        if not 1 <= result_bytes_reservation <= 16 * 1024 * 1024:
            raise CapabilityConfigurationError("pod_result_reservation_invalid")
        self._executor = executor
        self._result_bytes_reservation = result_bytes_reservation

    def quota_usage(self, request: PodsInvokeRequest) -> Any:
        return usage(
            rows=1,
            bytes=self._result_bytes_reservation,
            object_bytes=(
                self._result_bytes_reservation
                if request.action.value == "read_artifact"
                else 0
            ),
        )

    async def invoke(self, request: PodsInvokeRequest) -> PodsInvokeResponse:
        envelope = request_envelope(request, PodsInvokeRequest)
        self._ensure_open()
        if not envelope.project_id:
            raise CapabilityAccessDenied("pod_project_scope_required")
        require_safe_text(request.target_ref, code="pod_target_ref_invalid", max_bytes=2048)
        identity = {
            "company_id": envelope.company_id,
            "project_id": envelope.project_id,
            "plugin_id": plugin_id(envelope),
            "plugin_instance_id": envelope.plugin_instance_id,
            "artifact_digest": envelope.artifact_digest,
            "grant_id": envelope.grant_id,
            "run_id": envelope.run_id or "",
            "pod_id": request.pod_id,
            "action": request.action.value,
            "target_ref": request.target_ref,
            "arguments_digest": request.arguments_digest,
            "request_id": envelope.request_id,
            "max_result_bytes": self._result_bytes_reservation,
        }
        result = await await_io(
            self._executor.execute(identity=identity),
            timeout=self._timeout_seconds,
            code="pod_action_failed",
        )
        if not isinstance(result, Mapping):
            raise CapabilityBackendError("pod_action_invalid_result")
        for key in (
            "company_id", "project_id", "plugin_id", "plugin_instance_id",
            "artifact_digest", "grant_id", "run_id", "pod_id", "action",
            "target_ref", "arguments_digest", "request_id",
        ):
            expected = identity[key]
            if result.get(key) != expected:
                raise CapabilityAccessDenied("pod_action_tenant_binding_mismatch")
        bytes_received = result.get("bytes_received", 0)
        if (
            not isinstance(bytes_received, int)
            or isinstance(bytes_received, bool)
            or not 0 <= bytes_received <= self._result_bytes_reservation
        ):
            raise CapabilityBackendError("pod_action_result_byte_limit_exceeded")
        result_digest = result.get("result_digest")
        if not isinstance(result_digest, str) or not result_digest.startswith("sha256:"):
            raise CapabilityBackendError("pod_action_result_digest_missing")
        try:
            return PodsInvokeResponse(
                request_id=envelope.request_id,
                ok=True,
                digest=canonical_digest({
                    "pod_id": request.pod_id,
                    "action": request.action.value,
                    "result_digest": result_digest,
                }),
                pod_id=request.pod_id,
                action=request.action,
                result_digest=result_digest,
            )
        except ValueError:
            raise CapabilityBackendError("pod_action_result_digest_invalid") from None


__all__ = ["PodActionExecutor", "ScopedPodsAdapter"]
