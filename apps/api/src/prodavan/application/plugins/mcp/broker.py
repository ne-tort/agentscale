"""Manifest-scoped MCP backend broker; no infrastructure-capability remapping."""
from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    canonical_digest,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    McpInvokeRequest,
    McpInvokeResponse,
)
from prodavan.application.plugins.gateway.errors import GatewayError
from prodavan.application.plugins.gateway.ports import (
    GatewayClaims,
    GatewayGrant,
)
from prodavan.application.plugins.mcp.rpc import (
    SignedRpcOperationError,
    resolve_signed_rpc_operation,
)
from prodavan.application.plugins.mcp.discovery import (
    DurableMcpDiscovery,
    McpBinding,
    McpDiscoverRequest,
    McpDiscoveryError,
    _canonical,
    _redact_text,
    _strict_json,
)


_FORBIDDEN_IDENTITY_ARGUMENTS = frozenset({
    "plugin_id", "plugin_instance_id", "grant_id", "artifact_digest",
    "envelope", "scopes",
})
_SENSITIVE_FIELDS = frozenset({
    "access_key", "access_token", "api_key", "authorization", "credential",
    "credentials", "database_url", "dsn", "passwd", "password", "presigned_url",
    "private_key", "secret", "secret_key", "session_token", "token",
})


@dataclass(frozen=True, slots=True)
class _BrokerClaims:
    binding: McpBinding

    @property
    def company_id(self) -> str: return self.binding.company_id
    @property
    def plugin_id(self) -> str: return self.binding.plugin_id
    @property
    def plugin_instance_id(self) -> str: return self.binding.plugin_instance_id
    @property
    def artifact_digest(self) -> str: return self.binding.artifact_digest
    @property
    def manifest_version(self) -> str: return self.binding.manifest_version
    @property
    def grant_id(self) -> str: return self.binding.grant_id
    @property
    def sub(self) -> str:
        return (
            f"plugin:{self.binding.plugin_id}:{self.binding.company_id}:"
            f"{self.binding.plugin_instance_id}"
        )


def _binding(request: McpInvokeRequest) -> McpBinding:
    envelope = request.envelope
    parts = envelope.subject_id.split(":")
    if (
        len(parts) != 4
        or parts[0] != "plugin"
        or not parts[1]
        or parts[2] != envelope.company_id
        or parts[3] != envelope.plugin_instance_id
    ):
        raise CapabilityAccessDenied("mcp_binding_denied")
    return McpBinding(
        company_id=envelope.company_id,
        plugin_id=parts[1],
        plugin_instance_id=envelope.plugin_instance_id,
        artifact_digest=envelope.artifact_digest,
        manifest_version=request.manifest_version,
        grant_id=envelope.grant_id,
    )


def _arguments(request: McpInvokeRequest, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    raw = request.arguments_json.encode("utf-8")
    if len(raw) > max_bytes:
        raise CapabilityAccessDenied("mcp_arguments_cap_exceeded")
    expected = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected != request.arguments_digest:
        raise CapabilityAccessDenied("mcp_arguments_digest_mismatch")
    try:
        value = _strict_json(json.loads(raw), code="mcp_arguments_invalid")
        if not isinstance(value, dict) or _canonical(
            value, code="mcp_arguments_invalid"
        ) != raw:
            raise CapabilityAccessDenied("mcp_arguments_not_canonical")
    except McpDiscoveryError as exc:
        raise CapabilityAccessDenied(exc.code) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CapabilityAccessDenied("mcp_arguments_invalid") from None
    return value, raw


def _validate_schema(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        raise CapabilityConfigurationError("mcp_jsonschema_required") from None
    try:
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
        validator_type(schema).validate(arguments)
    except Exception:
        raise CapabilityAccessDenied("mcp_arguments_schema_mismatch") from None


def _assert_tenant_arguments(
    arguments: Mapping[str, Any], envelope: CapabilityEnvelope
) -> None:
    if _FORBIDDEN_IDENTITY_ARGUMENTS & set(arguments):
        raise CapabilityAccessDenied("mcp_identity_argument_forbidden")
    for name in ("company_id", "cabinet_id", "project_id"):
        if name in arguments and arguments[name] != getattr(envelope, name):
            raise CapabilityAccessDenied("mcp_tenant_assertion_mismatch")


def _query(arguments: Mapping[str, Any]) -> bytes:
    pairs: list[tuple[str, str]] = []
    for key in sorted(arguments):
        value = arguments[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            rendered = str(value)
        else:
            raise CapabilityAccessDenied("mcp_get_argument_not_scalar")
        pairs.append((key, rendered))
    return urlencode(pairs).encode("ascii")


def _redact(value: Any, field_name: str = "") -> Any:
    if field_name.lower().replace("-", "_") in _SENSITIVE_FIELDS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            key: _redact(child, key) if isinstance(key, str) else child
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact(child) for child in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class DurableMcpBroker:
    """Resolve registered MCP tools to unique signed backend RPC operations only."""

    def __init__(
        self,
        *,
        discovery: DurableMcpDiscovery,
        manifest_source: Any,
        grant_store: Any,
        backend_proxy: Any,
        max_arguments_bytes: int = 64 * 1024,
        max_response_bytes: int = 64 * 1024,
    ) -> None:
        if not callable(getattr(discovery, "discover", None)) or not callable(
            getattr(manifest_source, "load", None)
        ) or not callable(getattr(grant_store, "get_by_grant_id", None)) or not callable(
            getattr(backend_proxy, "forward", None)
        ):
            raise CapabilityConfigurationError("mcp_broker_runtime_required")
        if not 1 <= max_arguments_bytes <= 1024 * 1024 or not (
            1 <= max_response_bytes <= 1024 * 1024
        ):
            raise CapabilityConfigurationError("mcp_broker_cap_invalid")
        self._discovery = discovery
        self._manifest_source = manifest_source
        self._grant_store = grant_store
        self._backend_proxy = backend_proxy
        self._max_arguments_bytes = int(max_arguments_bytes)
        self._max_response_bytes = int(max_response_bytes)

    @property
    def timeout_seconds(self) -> float:
        """Backend proxy owns the longer forwarding deadline; stay out of its way."""
        return 30.0

    async def _verified_grant(
        self, binding: McpBinding
    ) -> tuple[frozenset[str], GatewayGrant]:
        """Project an exact active durable record; never mint authorization proof."""
        try:
            record = await self._grant_store.get_by_grant_id(
                grant_id=binding.grant_id
            )
        except Exception:
            raise CapabilityBackendError("mcp_grant_read_failed") from None
        if record is None:
            raise CapabilityAccessDenied("mcp_binding_denied")
        for name in (
            "grant_id", "company_id", "plugin_id", "plugin_instance_id",
            "artifact_digest", "manifest_version",
        ):
            if getattr(record, name, None) != getattr(binding, name):
                raise CapabilityAccessDenied("mcp_binding_denied")
        status = getattr(record, "status", None)
        active = getattr(record, "is_active", None)
        revoked_at = getattr(record, "revoked_at", None)
        if (
            status is not GrantStatus.ACTIVE
            or active is not True
            or revoked_at is not None
        ):
            raise CapabilityAccessDenied("mcp_binding_denied")
        now = datetime.now(UTC)
        for name, is_expiry in (("issued_at", False), ("expires_at", True)):
            value = getattr(record, name, None)
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise CapabilityAccessDenied("mcp_binding_denied")
            if value is not None and (
                (is_expiry and now >= value) or (not is_expiry and now < value)
            ):
                raise CapabilityAccessDenied("mcp_binding_denied")
        raw_operations = getattr(record, "capabilities", None)
        if isinstance(raw_operations, (str, bytes)):
            raise CapabilityAccessDenied("mcp_binding_denied")
        try:
            operations = frozenset(str(item) for item in raw_operations)
        except (TypeError, ValueError):
            raise CapabilityAccessDenied("mcp_binding_denied") from None
        if "mcp" not in operations:
            raise CapabilityAccessDenied("mcp_binding_denied")
        # Transport-only projection: active flag and operations come from the
        # verified durable row, not from caller/tool/manifest input.
        return operations, GatewayGrant(
            grant_id=binding.grant_id,
            active=active,
            operations=operations,
            plugin_instance_id=binding.plugin_instance_id,
            artifact_digest=binding.artifact_digest,
        )

    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResponse:
        if not isinstance(request, McpInvokeRequest) or not isinstance(
            request.envelope, CapabilityEnvelope
        ):
            raise CapabilityAccessDenied("typed_request_required")
        arguments, raw_arguments = _arguments(request, self._max_arguments_bytes)
        _assert_tenant_arguments(arguments, request.envelope)
        binding = _binding(request)
        claims = _BrokerClaims(binding)
        try:
            discovered = await self._discovery.discover(
                McpDiscoverRequest(server_id=request.server_id, claims=claims)
            )
        except McpDiscoveryError as exc:
            raise CapabilityAccessDenied(exc.code) from None
        selected = next(
            (tool for tool in discovered.tools if tool.name == request.tool_name), None
        )
        if selected is None:
            raise CapabilityAccessDenied("mcp_tool_unknown")
        if selected.capability != "mcp" or selected.operation != "mcp.invoke":
            raise CapabilityAccessDenied("mcp_infra_mapping_forbidden")
        try:
            signed = await self._manifest_source.load(claims=claims)
        except Exception:
            raise CapabilityBackendError("mcp_manifest_read_failed") from None
        if signed is None or signed.binding != binding:
            raise CapabilityAccessDenied("mcp_binding_denied")
        try:
            operation = resolve_signed_rpc_operation(
                signed.manifest,
                server_id=request.server_id,
                tool_name=request.tool_name,
            )
        except SignedRpcOperationError as exc:
            raise CapabilityAccessDenied(exc.code) from None
        declaration = next(
            (
                tool
                for server in signed.manifest.get("mcp_servers", [])
                if isinstance(server, Mapping)
                and server.get("server_id") == request.server_id
                for tool in server.get("tools", [])
                if isinstance(tool, Mapping) and tool.get("name") == request.tool_name
            ),
            None,
        )
        if declaration is None or not isinstance(declaration.get("input_schema"), Mapping):
            raise CapabilityAccessDenied("mcp_manifest_conflict")
        _validate_schema(declaration["input_schema"], arguments)

        body = raw_arguments if operation.http_method == "POST" else b""
        query = _query(arguments) if operation.http_method == "GET" else b""
        durable_operations, verified_grant = await self._verified_grant(binding)
        gateway_claims = GatewayClaims(
            plugin_id=binding.plugin_id,
            company_id=binding.company_id,
            subject_id=request.envelope.subject_id,
            scopes=durable_operations,
            project_id=request.envelope.project_id,
            grant_id=binding.grant_id,
            anonymous=False,
            plugin_instance_id=binding.plugin_instance_id,
            artifact_digest=binding.artifact_digest,
        )
        try:
            response = self._backend_proxy.forward(
                path=operation.backend_path,
                method=operation.http_method,
                body=body,
                headers={
                    "Accept": "application/json",
                    **({"Content-Type": "application/json"} if body else {}),
                },
                claims=gateway_claims,
                grant=verified_grant,
                stream=False,
                request_id=request.envelope.request_id,
                query_string=query,
            )
            if inspect.isawaitable(response):
                response = await response
        except GatewayError as exc:
            # Preserve only the stable public code; backend detail stays private.
            raise CapabilityBackendError(exc.gateway_code.value) from None
        except Exception:
            raise CapabilityBackendError("mcp_backend_invoke_failed") from None
        if (
            not 200 <= getattr(response, "status_code", 0) <= 299
            or getattr(response, "stream", None) is not None
        ):
            raise CapabilityBackendError("mcp_backend_response_invalid")
        raw_response = getattr(response, "body", None)
        if raw_response is None:
            raw_response = b""
        if not isinstance(raw_response, bytes) or len(raw_response) > self._max_response_bytes:
            raise CapabilityBackendError("mcp_response_cap_exceeded")
        try:
            parsed = json.loads(raw_response) if raw_response else None
            safe = _redact(_strict_json(parsed, code="mcp_gateway_response_invalid"))
            response_bytes = _canonical(
                safe, code="mcp_gateway_response_invalid"
            )
        except (McpDiscoveryError, UnicodeDecodeError, json.JSONDecodeError):
            raise CapabilityBackendError("mcp_backend_response_invalid") from None
        if len(response_bytes) > self._max_response_bytes:
            raise CapabilityBackendError("mcp_response_cap_exceeded")
        return McpInvokeResponse(
            request_id=request.envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "binding": binding.registry_scope,
                "artifact_digest": binding.artifact_digest,
                "registration_digest": discovered.registration_digest,
                "operation": operation.operation,
                "backend_path": operation.backend_path,
                "result": safe,
            }),
            server_id=request.server_id,
            tool_name=request.tool_name,
            operation=operation.operation,
            backend_path=operation.backend_path,
            status_code=response.status_code,
            arguments_bytes=len(raw_arguments),
            response_bytes=len(response_bytes),
            response_json=response_bytes.decode("utf-8"),
        )


__all__ = ["DurableMcpBroker"]
