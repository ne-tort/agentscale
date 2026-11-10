"""Declaration-bound egress through the canonical plugin backend Service.

No request host is used as a Kubernetes/API destination. The lifecycle-scoped
client calls only the canonical Service proxy at /v1/egress with typed JSON and
SHA-256 body references. Grant declarations, backend ownership and both image
and bundle digests are revalidated on every call.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from prodavan.application.plugins.admin.contracts import CapabilityGrantRecord
from prodavan.application.plugins.egress.policy import (
    EgressPolicy,
    match,
    normalize_host,
    normalize_path,
    validate_host,
)
from prodavan.application.plugins.egress.resolver import GrantLookup, GrantPolicyResolver
from prodavan.application.plugins.egress.service import EgressService

from .config import RuntimeConfig
from .errors import RuntimeConfigError
from .pod_executor import (
    ArtifactStore,
    CanonicalBackendResolver,
    InstanceStore,
    PodExecutorError,
    _canonical_json,
    _digest,
    _text,
    _plugin_capability_headers,
    _service_proxy_request,
    backend_response_body,
    service_proxy_path,
)

_EMPTY_DIGEST = "sha256:" + "0" * 64
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_MAX_PROXY_JSON_BYTES = 64 * 1024
_MAX_TRANSPORT_BYTES = 16 * 1024 * 1024
_RESPONSE_IDENTITY_FIELDS = (
    "company_id",
    "plugin_id",
    "plugin_instance_id",
    "artifact_digest",
    "backend_digest",
    "grant_id",
    "subject_id",
    "request_id",
    "host",
    "port",
    "path",
    "method",
    "body_digest",
)


class EgressTransportError(PodExecutorError):
    """Sanitized transport/declaration failure."""


class GrantStore(Protocol):
    async def get_by_grant_id(self, *, grant_id: str) -> CapabilityGrantRecord | None: ...


@dataclass(frozen=True, slots=True)
class EgressDeclaration:
    policy: EgressPolicy

    @property
    def max_egress_bytes(self) -> int:
        return self.policy.max_egress_bytes

    def allows(self, *, host: str, port: int, path: str, method: str) -> bool:
        if port != 443 or method not in _METHODS or self.max_egress_bytes < 1:
            return False
        try:
            safe_host = validate_host(normalize_host(host), allow_wildcard=False)
            safe_path = normalize_path(path)
        except Exception:
            return False
        return any(
            match(rule, safe_host, safe_path, method) for rule in self.policy.rules
        )


class EgressDeclarationPort(Protocol):
    async def declaration(self, *, identity: Mapping[str, Any]) -> EgressDeclaration: ...


class DurableGrantEgressPolicy:
    """Read grant declarations without the slotted-dataclass mapping bug."""

    def __init__(self, store: GrantStore, *, hop_inspector: Any = None) -> None:
        if not callable(getattr(store, "get_by_grant_id", None)):
            raise RuntimeConfigError("durable egress grant store is required")
        self._store = store
        self._hop_inspector = hop_inspector

    async def _lookup(self, grant_id: str) -> tuple[CapabilityGrantRecord, GrantLookup]:
        cleaned = _text(grant_id, code="egress_grant_required", max_length=256)
        try:
            record = await self._store.get_by_grant_id(grant_id=cleaned)
        except Exception:
            raise EgressTransportError("egress_grant_lookup_failed") from None
        if not isinstance(record, CapabilityGrantRecord):
            raise EgressTransportError("egress_grant_missing")
        policy = record.egress_policy
        quotas = record.quotas
        raw = {
            "grant_id": record.grant_id,
            "egress": {
                "mode": policy.mode.value,
                "allowed_hosts": list(policy.allowed_hosts),
                "allowed_paths": list(policy.allowed_paths),
                "approved": policy.approved_for_full,
                "max_egress_bytes": quotas.max_egress_bytes,
            },
        }
        lookup = GrantLookup(
            raw=raw,
            active=record.is_active and record.revoked_at is None,
            revoked_at=record.revoked_at,
            issued_at=getattr(record, "issued_at", record.created_at),
            expires_at=getattr(record, "expires_at", None),
        )
        return record, lookup

    @staticmethod
    def _policy(lookup: GrantLookup, grant_id: str) -> EgressPolicy:
        class StaticStore:
            def get_grant(self, requested: str) -> GrantLookup:
                if requested != grant_id:
                    raise LookupError("grant binding mismatch")
                return lookup

        try:
            return GrantPolicyResolver(StaticStore()).resolve(grant_id)
        except Exception:
            raise EgressTransportError("egress_declaration_invalid") from None

    async def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool:
        """Cheap initial fence; transport subsequently checks the exact method."""
        _, lookup = await self._lookup(grant_id)
        policy = self._policy(lookup, grant_id)
        if port != 443 or not policy.allows_any or policy.max_egress_bytes < 1:
            return False
        try:
            safe_host = validate_host(normalize_host(host), allow_wildcard=False)
            safe_path = normalize_path(path)
        except Exception:
            return False
        return any(
            match(rule, safe_host, safe_path, method)
            for rule in policy.rules
            for method in _METHODS
        )

    async def check(self, *args: Any, **kwargs: Any) -> Any:
        claims = args[0] if args else kwargs.get("claims")
        if not isinstance(claims, Mapping):
            raise EgressTransportError("egress_claims_required")
        grant_id = claims.get("grant_id")
        if not isinstance(grant_id, str):
            raise EgressTransportError("egress_grant_required")
        _, lookup = await self._lookup(grant_id)

        class StaticStore:
            def get_grant(self, requested: str) -> GrantLookup:
                if requested != grant_id:
                    raise LookupError("grant binding mismatch")
                return lookup

        service = EgressService(store=StaticStore(), hop_inspector=self._hop_inspector)
        return service.check(*args, **kwargs)

    async def declaration(self, *, identity: Mapping[str, Any]) -> EgressDeclaration:
        grant_id = _text(identity.get("grant_id"), code="egress_grant_required", max_length=256)
        record, lookup = await self._lookup(grant_id)
        expected = (
            ("company_id", record.company_id),
            ("plugin_id", record.plugin_id),
            ("plugin_instance_id", record.plugin_instance_id),
            ("artifact_digest", record.artifact_digest),
            ("grant_id", record.grant_id),
        )
        if (
            not record.is_active
            or record.revoked_at is not None
            or "network" not in record.capabilities
            or any(identity.get(key) != value for key, value in expected)
        ):
            raise EgressTransportError("egress_grant_identity_mismatch")
        return EgressDeclaration(self._policy(lookup, grant_id))


class K8sEgressTransport:
    """Scoped Kubernetes Service-proxy transport with hard declaration bounds."""

    scoped_identity = True
    digest_bound_channel = True

    def __init__(
        self,
        *,
        resolver: CanonicalBackendResolver | None = None,
        declarations: EgressDeclarationPort | None = None,
        config: RuntimeConfig | None = None,
        instance_store: InstanceStore | None = None,
        artifact_store: ArtifactStore | None = None,
        grant_store: GrantStore | None = None,
        api: Any = None,
        token_provider: Any = None,
        activator: Any = None,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        if resolver is None:
            resolver = CanonicalBackendResolver(
                config=config,  # type: ignore[arg-type]
                instance_store=instance_store,  # type: ignore[arg-type]
                artifact_store=artifact_store,  # type: ignore[arg-type]
                api=api,
                token_provider=token_provider,
            )
        elif any(
            value is not None
            for value in (config, instance_store, artifact_store, api, token_provider)
        ):
            raise RuntimeConfigError("egress resolver and its dependencies are exclusive")
        if declarations is None:
            declarations = DurableGrantEgressPolicy(grant_store)  # type: ignore[arg-type]
        elif grant_store is not None:
            raise RuntimeConfigError("egress declarations and grant_store are exclusive")
        if not callable(getattr(declarations, "declaration", None)):
            raise RuntimeConfigError("typed egress declaration port is required")
        for name, value in (
            ("request", max_request_bytes),
            ("response", max_response_bytes),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= _MAX_TRANSPORT_BYTES
            ):
                raise RuntimeConfigError(f"egress {name} byte limit is invalid")
        if not callable(getattr(activator, "wake", None)):
            raise RuntimeConfigError("fresh PluginActivator wake is required")
        self._resolver = resolver
        self._activator = activator
        self._declarations = declarations
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._closed = False

    @property
    def max_request_bytes(self) -> int:
        return self._max_request_bytes

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    def _route(self, identity: Mapping[str, Any]) -> tuple[str, int, str, str, str, int]:
        host = _text(identity.get("host"), code="egress_host_invalid", max_length=253)
        port = identity.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or port != 443:
            raise EgressTransportError("egress_port_not_declared")
        path = _text(identity.get("path"), code="egress_path_invalid")
        method = _text(identity.get("method"), code="egress_method_invalid", max_length=16)
        if method not in _METHODS:
            raise EgressTransportError("egress_method_not_declared")
        body_digest = _digest(identity.get("body_digest"), code="egress_body_digest_invalid")
        max_response = identity.get("max_response_bytes", self._max_response_bytes)
        if (
            not isinstance(max_response, int)
            or isinstance(max_response, bool)
            or not 1 <= max_response <= self._max_response_bytes
        ):
            raise EgressTransportError("egress_response_cap_invalid")
        return host, port, path, method, body_digest, max_response

    async def execute(self, *, identity: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._execute(identity=identity, body=b"")

    async def execute_with_body(
        self, *, identity: Mapping[str, Any], body: bytes
    ) -> Mapping[str, Any]:
        if not isinstance(body, bytes):
            raise EgressTransportError("egress_body_bytes_required")
        return await self._execute(identity=identity, body=body)

    async def _execute(
        self, *, identity: Mapping[str, Any], body: bytes
    ) -> Mapping[str, Any]:
        if self._closed:
            raise EgressTransportError("egress_transport_closed")
        backend = await self._resolver.resolve(identity)
        host, port, path, method, body_digest, requested_response_cap = self._route(identity)
        try:
            declaration = await self._declarations.declaration(identity=identity)
        except PodExecutorError:
            raise
        except Exception:
            raise EgressTransportError("egress_declaration_failed") from None
        if not isinstance(declaration, EgressDeclaration) or not declaration.allows(
            host=host, port=port, path=path, method=method
        ):
            raise EgressTransportError("egress_route_not_declared")
        response_cap = min(requested_response_cap, declaration.max_egress_bytes)
        request_cap = min(self._max_request_bytes, declaration.max_egress_bytes)
        if not isinstance(body, bytes) or len(body) > request_cap:
            raise EgressTransportError("egress_body_byte_limit_exceeded")
        actual_body_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if body_digest == _EMPTY_DIGEST:
            if body:
                raise EgressTransportError("egress_body_digest_binding_mismatch")
        elif not body or not hmac.compare_digest(actual_body_digest, body_digest):
            raise EgressTransportError("egress_body_digest_binding_mismatch")
        request_id = _text(identity.get("request_id"), code="egress_request_id_required", max_length=256)
        subject_id = _text(identity.get("subject_id"), code="egress_subject_required", max_length=512)
        payload = {
            "company_id": backend.company_id,
            "plugin_id": backend.plugin_id,
            "plugin_instance_id": backend.plugin_instance_id,
            "artifact_digest": backend.artifact_digest,
            "backend_digest": backend.backend_digest,
            "grant_id": declaration.policy.grant_id,
            "subject_id": subject_id,
            "request_id": request_id,
            "host": host,
            "port": port,
            "path": path,
            "method": method,
            "body_digest": body_digest,
            "body_base64": base64.b64encode(body).decode("ascii"),
            "max_request_bytes": request_cap,
            "max_response_bytes": response_cap,
        }
        if not isinstance(payload["grant_id"], str) or payload["grant_id"] != identity.get("grant_id"):
            raise EgressTransportError("egress_grant_identity_mismatch")
        proxy_cap = _MAX_PROXY_JSON_BYTES + 4 * ((request_cap + 2) // 3)
        if len(_canonical_json(payload)) > proxy_cap:
            raise EgressTransportError("egress_proxy_request_too_large")
        try:
            plugin_headers = await _plugin_capability_headers(
                activator=self._activator,
                backend=backend,
                grant_id=payload["grant_id"],
                project_id=(
                    identity.get("project_id")
                    if isinstance(identity.get("project_id"), str)
                    else None
                ),
                required_scope="network",
                error_code="egress_backend_activation_failed",
            )
        except PodExecutorError as exc:
            raise EgressTransportError(exc.code) from None
        try:
            response = await _service_proxy_request(
                self._resolver._api,
                method="POST",
                path=service_proxy_path(backend, "/v1/egress"),
                payload=payload,
                plugin_headers=plugin_headers,
            )
        except Exception:
            raise EgressTransportError("egress_backend_request_failed") from None
        # Keep the outbound mapping separate from the typed backend response:
        # bytes_sent must prove the original payload, not response-map size.
        response_body = backend_response_body(response)
        for key in _RESPONSE_IDENTITY_FIELDS:
            if response_body.get(key) != payload.get(key):
                raise EgressTransportError("egress_backend_identity_mismatch")
        status = response_body.get("status_code")
        bytes_sent = response_body.get("bytes_sent")
        bytes_received = response_body.get("bytes_received")
        response_digest = response_body.get("response_digest")
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 100 <= status <= 599
            or not isinstance(bytes_sent, int)
            or isinstance(bytes_sent, bool)
            or not isinstance(bytes_received, int)
            or isinstance(bytes_received, bool)
            or bytes_sent < 0
            or bytes_received < 0
            or bytes_sent > request_cap
            or bytes_received > response_cap
            or bytes_sent + bytes_received > declaration.max_egress_bytes
        ):
            raise EgressTransportError("egress_backend_quota_bounds_invalid")
        if bytes_sent != len(body):
            raise EgressTransportError("egress_body_digest_binding_mismatch")
        _digest(response_digest, code="egress_response_digest_invalid")
        try:
            resolved_host = validate_host(
                normalize_host(response_body.get("resolved_host")), allow_wildcard=False
            )
        except Exception:
            raise EgressTransportError("egress_resolved_host_invalid") from None
        raw_hops = response_body.get("hops")
        if not isinstance(raw_hops, (list, tuple)) or any(
            not isinstance(item, str) for item in raw_hops
        ):
            raise EgressTransportError("egress_hops_invalid")
        hops = tuple(raw_hops)
        if response_body.get("redirect_checked") is not True or response_body.get("tls_verified") is not True:
            raise EgressTransportError("egress_transport_evidence_incomplete")
        return {
            "company_id": backend.company_id,
            "plugin_id": backend.plugin_id,
            "plugin_instance_id": backend.plugin_instance_id,
            "artifact_digest": backend.artifact_digest,
            "backend_digest": backend.backend_digest,
            "grant_id": payload["grant_id"],
            "subject_id": subject_id,
            "request_id": request_id,
            "run_id": identity.get("run_id", ""),
            "host": host,
            "port": port,
            "path": path,
            "method": method,
            "body_digest": body_digest,
            "status_code": status,
            "bytes_sent": bytes_sent,
            "bytes_received": bytes_received,
            "response_digest": response_digest,
            "resolved_host": resolved_host,
            "hops": hops,
            "redirect_checked": True,
            "tls_verified": True,
            "max_request_bytes": request_cap,
            "max_response_bytes": response_cap,
        }

    async def aclose(self) -> None:
        """Close local state once; shared lifecycle API/token owner closes them."""
        if self._closed:
            return
        self._closed = True


# Explicit descriptive alias for wiring/readability; both names are one type.
K8sServiceProxyEgressTransport = K8sEgressTransport


__all__ = [
    "DurableGrantEgressPolicy",
    "EgressDeclaration",
    "EgressDeclarationPort",
    "EgressTransportError",
    "GrantStore",
    "K8sEgressTransport",
    "K8sServiceProxyEgressTransport",
]
