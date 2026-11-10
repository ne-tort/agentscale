"""Egress through a scoped Kubernetes proxy with per-hop policy verification."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Protocol

from prodavan.application.plugins.capabilities.contracts import (
    NetworkEgressRequest,
    NetworkEgressResponse,
    empty_digest,
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
)


class EgressTransport(Protocol):
    async def execute(self, *, identity: Mapping[str, Any]) -> Mapping[str, Any]: ...


class K8sEgressNetworkAdapter(CloseOnce):
    def __init__(
        self,
        *,
        transport: EgressTransport,
        egress: Any,
        initial_policy: Any,
        body_digest_resolver: Any = None,
        max_response_bytes: int = 1024 * 1024,
        timeout_seconds: float = 20.0,
        owns_transport: bool = True,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(transport,) if owns_transport else (),
        )
        if transport is None or not callable(getattr(transport, "execute", None)):
            raise CapabilityConfigurationError("k8s_egress_transport_required")
        if getattr(transport, "scoped_identity", False) is not True:
            raise CapabilityConfigurationError("default_mounted_service_account_forbidden")
        if egress is None or not callable(getattr(egress, "check", None)):
            raise CapabilityConfigurationError("egress_policy_service_required")
        if initial_policy is None or not callable(getattr(initial_policy, "allows", None)):
            raise CapabilityConfigurationError("initial_egress_policy_required")
        if not 1 <= max_response_bytes <= 16 * 1024 * 1024:
            raise CapabilityConfigurationError("egress_response_cap_invalid")
        max_request_bytes = getattr(transport, "max_request_bytes", 0)
        if (
            not isinstance(max_request_bytes, int)
            or isinstance(max_request_bytes, bool)
            or not 0 <= max_request_bytes <= 16 * 1024 * 1024
        ):
            raise CapabilityConfigurationError("egress_request_cap_invalid")
        if body_digest_resolver is not None and not callable(
            getattr(body_digest_resolver, "resolve_body_digest", None)
        ):
            raise CapabilityConfigurationError("network_payload_resolver_not_configured")
        self._transport = transport
        self._egress = egress
        self._initial_policy = initial_policy
        self._body_digest_resolver = body_digest_resolver
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes

    def quota_usage(self, request: NetworkEgressRequest) -> Any:
        return usage(
            egress_bytes=self._max_request_bytes + self._max_response_bytes
        )

    async def _resolve_body(self, request: NetworkEgressRequest) -> bytes:
        if request.body_digest == empty_digest():
            return b""
        resolver = self._body_digest_resolver
        if resolver is None:
            raise CapabilityAccessDenied("network_payload_resolver_not_configured")
        envelope = request.envelope
        body = await await_io(
            resolver.resolve_body_digest(
                envelope.company_id,
                plugin_id(envelope),
                envelope.plugin_instance_id,
                request.body_digest,
            ),
            timeout=self._timeout_seconds,
            code="network_payload_resolve_failed",
        )
        if body is None:
            raise CapabilityAccessDenied("network_payload_missing")
        if not isinstance(body, bytes) or not body:
            raise CapabilityAccessDenied("network_payload_invalid")
        if len(body) > self._max_request_bytes:
            raise CapabilityAccessDenied("network_payload_too_large")
        actual_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(actual_digest, request.body_digest):
            raise CapabilityAccessDenied("network_payload_digest_mismatch")
        return body

    async def egress(self, request: NetworkEgressRequest) -> NetworkEgressResponse:
        envelope = request_envelope(request, NetworkEgressRequest)
        self._ensure_open()
        if (
            request.body_digest != empty_digest()
            and getattr(self._transport, "digest_bound_channel", False) is not True
        ):
            # Raw bodies never enter this contract. Only a transport that proves
            # a digest-bound backend channel may accept a non-empty reference.
            raise CapabilityAccessDenied("network_payload_resolver_not_configured")
        allowed = await await_io(
            self._initial_policy.allows(
                grant_id=envelope.grant_id,
                host=request.host,
                port=request.port,
                path=request.path,
            ),
            timeout=self._timeout_seconds,
            code="egress_initial_policy_failed",
        )
        if allowed is not True:
            raise CapabilityAccessDenied("network_initial_host_denied")
        body = await self._resolve_body(request)
        identity: dict[str, Any] = {
            "company_id": envelope.company_id,
            "plugin_instance_id": envelope.plugin_instance_id,
            "plugin_id": plugin_id(envelope),
            "artifact_digest": envelope.artifact_digest,
            "grant_id": envelope.grant_id,
            "subject_id": envelope.subject_id,
            "request_id": envelope.request_id,
            "run_id": envelope.run_id or "",
            "host": request.host,
            "port": request.port,
            "path": request.path,
            "method": request.method.value,
            "body_digest": request.body_digest,
            "max_request_bytes": self._max_request_bytes,
            "max_response_bytes": self._max_response_bytes,
        }
        if body:
            execute_with_body = getattr(self._transport, "execute_with_body", None)
            if not callable(execute_with_body):
                raise CapabilityAccessDenied("network_payload_transport_not_configured")
            invocation = execute_with_body(identity=identity, body=body)
        else:
            invocation = self._transport.execute(identity=identity)
        result = await await_io(
            invocation,
            timeout=self._timeout_seconds,
            code="k8s_egress_transport_failed",
        )
        if not isinstance(result, Mapping):
            raise CapabilityBackendError("egress_transport_invalid_result")
        for key in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "grant_id", "subject_id", "request_id", "run_id", "host", "port",
            "path", "method", "body_digest",
        ):
            if result.get(key) != identity[key]:
                raise CapabilityAccessDenied("egress_transport_identity_mismatch")
        try:
            status = int(result["status_code"])
            bytes_sent = int(result["bytes_sent"])
            bytes_received = int(result["bytes_received"])
            response_digest = str(result["response_digest"])
            resolved_host = result.get("resolved_host")
            hops = result.get("hops", ())
            redirect_checked = result.get("redirect_checked") is True
            tls_verified = result.get("tls_verified") is True
        except (KeyError, TypeError, ValueError):
            raise CapabilityBackendError("egress_transport_invalid_result") from None
        if (
            not 100 <= status <= 599
            or bytes_sent < 0
            or bytes_sent > self._max_request_bytes
            or bytes_received < 0
            or bytes_received > self._max_response_bytes
            or bytes_sent + bytes_received > self._max_request_bytes + self._max_response_bytes
            or bytes_sent != len(body)
            or not response_digest.startswith("sha256:")
            or isinstance(hops, (str, bytes))
        ):
            raise CapabilityBackendError("egress_transport_unsafe_result")
        decision = await await_io(
            self._egress.check(
                {
                    "company_id": envelope.company_id,
                    "sub": envelope.subject_id,
                    "grant_id": envelope.grant_id,
                },
                request.host,
                request.path,
                request.method.value,
                bytes_sent,
                resolved_host=resolved_host if isinstance(resolved_host, str) else None,
                hops=tuple(str(item) for item in hops),
                redirect_checked=redirect_checked,
                tls_verified=tls_verified,
            ),
            timeout=self._timeout_seconds,
            code="egress_per_hop_policy_failed",
        )
        if not decision.allowed:
            raise CapabilityAccessDenied("network_per_hop_policy_denied")
        return NetworkEgressResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "status_code": status,
                "response_digest": response_digest,
                "bytes_sent": bytes_sent,
                "bytes_received": bytes_received,
            }),
            status_code=status,
            response_digest=response_digest,
            bytes_sent=bytes_sent,
            bytes_received=bytes_received,
        )


__all__ = ["EgressTransport", "K8sEgressNetworkAdapter"]
