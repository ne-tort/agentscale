"""Durable, signed-manifest MCP tool discovery and typed gateway invocation.

No MCP network handshake is invented here.  The consumer is a downstream runtime
building block: it reads the existing Redis registration store and invokes only
through PluginCapabilityService, whose grant/auth/quota/audit pipeline remains
authoritative.  A live MCP client still needs an adapter for initialize and
JSON-RPC tools/list/tools/call.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.capabilities.adapters._shared import (
    CloseOnce,
    canonical_digest,
)
from prodavan.application.plugins.capabilities.adapters.mcp import (
    digest_bound_server_id,
)
from prodavan.application.plugins.mcp.rpc import (
    SignedRpcOperationError,
    resolve_signed_rpc_operation,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityName,
    McpInvokeResponse as CapabilityMcpInvokeResponse,
    McpToolDefinition,
    empty_digest,
)


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DSN = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|redis|rediss|mongodb(?:\+srv)?|"
    r"amqp|amqps|kafka)://[^\s]+"
)
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SENSITIVE_FIELDS = frozenset({
    "access_key", "access_token", "api_key", "authorization", "credential",
    "credentials", "database_url", "dsn", "passwd", "password", "presigned_url",
    "private_key", "secret", "secret_key", "session_token", "token",
})
_DEFINITION_FIELDS = frozenset({
    "tool_name", "tool_version", "capability", "operation", "input_schema_digest",
})
_MANIFEST_TOOL_FIELDS = frozenset({
    "name", "description", "version", "input_schema",
})
_MAX_DEPTH = 16
_MAX_NODES = 1024


class McpDiscoveryError(RuntimeError):
    """Safe stable error; backend text and arguments never cross this boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: Any, *, code: str, max_bytes: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > max_bytes
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise McpDiscoveryError(code)
    return value


def _redact_text(value: str) -> str:
    value = _DSN.sub("[REDACTED_DSN]", value)
    return _URL_USERINFO.sub(r"\1[REDACTED]@", value)


def _strict_json(value: Any, *, code: str) -> Any:
    """Copy JSON data with depth/node limits and reject non-JSON values."""
    nodes = 0

    def walk(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise McpDiscoveryError(code)
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise McpDiscoveryError(code)
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise McpDiscoveryError(code)
                result[key] = walk(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(child, depth + 1) for child in item]
        raise McpDiscoveryError(code)

    return walk(value, 0)


def _canonical(value: Any, *, code: str) -> bytes:
    safe = _strict_json(value, code=code)
    try:
        return json.dumps(
            safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise McpDiscoveryError(code) from None


def _schema_digest(schema: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical(schema, code="mcp_manifest_conflict")
    ).hexdigest()


def _claim(claims: Any, name: str) -> Any:
    value = getattr(claims, name, None)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise McpDiscoveryError("mcp_binding_denied")
    return value


@dataclass(frozen=True, slots=True)
class McpBinding:
    """Exact server-derived tenant/instance/immutable-artifact binding."""

    company_id: str
    plugin_id: str
    plugin_instance_id: str
    artifact_digest: str
    manifest_version: str
    grant_id: str

    @classmethod
    def from_claims(cls, claims: Any) -> "McpBinding":
        binding = cls(
            company_id=_claim(claims, "company_id"),
            plugin_id=_claim(claims, "plugin_id"),
            plugin_instance_id=_claim(claims, "plugin_instance_id"),
            artifact_digest=_claim(claims, "artifact_digest"),
            manifest_version=_claim(claims, "manifest_version"),
            grant_id=_claim(claims, "grant_id"),
        )
        expected_subject = (
            f"plugin:{binding.plugin_id}:{binding.company_id}:"
            f"{binding.plugin_instance_id}"
        )
        if _claim(claims, "sub") != expected_subject:
            raise McpDiscoveryError("mcp_binding_denied")
        return binding

    def __post_init__(self) -> None:
        for value in (
            self.company_id, self.plugin_id, self.plugin_instance_id,
            self.manifest_version, self.grant_id,
        ):
            _text(value, code="mcp_binding_denied")
        if not isinstance(self.artifact_digest, str) or not _DIGEST.fullmatch(
            self.artifact_digest
        ):
            raise McpDiscoveryError("mcp_binding_denied")

    @property
    def registry_scope(self) -> dict[str, str]:
        return {
            "company_id": self.company_id,
            "plugin_id": self.plugin_id,
            "plugin_instance_id": self.plugin_instance_id,
        }


@dataclass(frozen=True, slots=True)
class SignedMcpManifest:
    binding: McpBinding
    manifest: Mapping[str, Any]


class McpManifestSource(Protocol):
    async def load(self, *, claims: Any) -> SignedMcpManifest | None: ...


class McpRegistrationReader(Protocol):
    async def get(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str
    ) -> str | None: ...


class McpCapabilityGateway(Protocol):
    async def invoke(
        self, *, claims: Any, capability: str, operation: str, request: Any
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class McpDiscoverRequest:
    server_id: str
    claims: Any

    def __post_init__(self) -> None:
        _text(self.server_id, code="mcp_server_invalid")


@dataclass(frozen=True, slots=True)
class McpInvokeRequest:
    server_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    claims: Any

    def __post_init__(self) -> None:
        _text(self.server_id, code="mcp_server_invalid")
        _text(self.tool_name, code="mcp_tool_unknown")
        if not isinstance(self.arguments, Mapping):
            raise McpDiscoveryError("mcp_arguments_invalid")


@dataclass(frozen=True, slots=True)
class McpTool:
    server_id: str
    name: str
    version: str
    description: str
    input_schema: Mapping[str, Any]
    capability: str
    operation: str
    input_schema_digest: str
    artifact_digest: str
    registration_digest: str


@dataclass(frozen=True, slots=True)
class McpDiscoveryResult:
    binding: McpBinding
    server_id: str
    registration_digest: str
    tools: tuple[McpTool, ...]
    response_bytes: int


@dataclass(frozen=True, slots=True)
class McpUsage:
    """Measured MCP adaptation bytes, not a second quota reservation."""

    arguments_bytes: int
    response_bytes: int


@dataclass(frozen=True, slots=True)
class McpInvokeResult:
    binding: McpBinding
    server_id: str
    tool_name: str
    result: Any
    result_digest: str
    usage: McpUsage


@dataclass(frozen=True, slots=True)
class _Declaration:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, Any]
    input_schema_digest: str


def _require_backend_definition(definition: McpToolDefinition) -> None:
    """Redis is mutable data, never a source of privilege selection."""
    if (
        definition.capability is not CapabilityName.MCP
        or definition.operation != "mcp.invoke"
    ):
        raise McpDiscoveryError("mcp_privilege_remap_denied")


async def _call(method: Any, /, *, timeout: float, code: str, **kwargs: Any) -> Any:
    """Call sync/async ports with one sanitized deadline boundary."""
    try:
        async with asyncio.timeout(timeout):
            value = method(**kwargs)
            return await value if inspect.isawaitable(value) else value
    except McpDiscoveryError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise McpDiscoveryError(code + "_timeout") from None
    except Exception:
        raise McpDiscoveryError(code) from None


def _record_value(record: Any, name: str) -> Any:
    return record.get(name) if isinstance(record, Mapping) else getattr(record, name, None)


class ArtifactGrantMcpManifestSource:
    """Resolve only the claim's grant and its verified immutable artifact."""

    def __init__(
        self, *, grant_store: Any, artifact_registry: Any, timeout_seconds: float = 3.0
    ) -> None:
        if not callable(getattr(grant_store, "get_by_grant_id", None)) or not callable(
            getattr(artifact_registry, "get_async", None)
        ):
            raise McpDiscoveryError("mcp_manifest_source_required")
        if timeout_seconds <= 0:
            raise McpDiscoveryError("mcp_timeout_invalid")
        self._grant_store = grant_store
        self._artifact_registry = artifact_registry
        self._timeout = float(timeout_seconds)

    async def load(self, *, claims: Any) -> SignedMcpManifest | None:
        expected = McpBinding.from_claims(claims)
        record = await _call(
            self._grant_store.get_by_grant_id,
            grant_id=expected.grant_id,
            timeout=self._timeout,
            code="mcp_grant_read_failed",
        )
        if record is None:
            return None
        for name in (
            "company_id", "plugin_id", "plugin_instance_id", "artifact_digest",
            "manifest_version", "grant_id",
        ):
            if _record_value(record, name) != getattr(expected, name):
                raise McpDiscoveryError("mcp_binding_denied")
        status = _record_value(record, "status")
        active = _record_value(record, "is_active")
        if (
            status is not GrantStatus.ACTIVE
            or active is not True
            or _record_value(record, "revoked_at") is not None
        ):
            raise McpDiscoveryError("mcp_binding_denied")
        now = datetime.now(UTC)
        for name, is_expiry in (("issued_at", False), ("expires_at", True)):
            value = _record_value(record, name)
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise McpDiscoveryError("mcp_binding_denied")
            if value is not None and (
                (is_expiry and now >= value) or (not is_expiry and now < value)
            ):
                raise McpDiscoveryError("mcp_binding_denied")
        artifact = await _call(
            self._artifact_registry.get_async,
            plugin_id=expected.plugin_id,
            version=expected.manifest_version,
            timeout=self._timeout,
            code="mcp_manifest_read_failed",
        )
        if artifact is None:
            return None
        if (
            getattr(artifact, "plugin_id", None) != expected.plugin_id
            or getattr(artifact, "digest", None) != expected.artifact_digest
            or getattr(artifact, "published", False) is not True
            or getattr(artifact, "verification", None) is None
        ):
            raise McpDiscoveryError("mcp_binding_denied")
        manifest = getattr(artifact, "manifest", None)
        if not isinstance(manifest, Mapping):
            raise McpDiscoveryError("mcp_manifest_conflict")
        if (
            manifest.get("plugin_id") != expected.plugin_id
            or manifest.get("version") != expected.manifest_version
        ):
            raise McpDiscoveryError("mcp_binding_denied")
        return SignedMcpManifest(binding=expected, manifest=manifest)


class DurableMcpDiscovery(CloseOnce):
    """Intersection of signed declarations and exact-scope durable registrations."""

    def __init__(
        self,
        *,
        store: McpRegistrationReader,
        manifest_source: McpManifestSource,
        gateway: McpCapabilityGateway,
        max_arguments_bytes: int = 64 * 1024,
        max_response_bytes: int = 64 * 1024,
        max_tools: int = 64,
        timeout_seconds: float = 3.0,
        owns_store: bool = False,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(store,) if owns_store else (),
        )
        if not callable(getattr(store, "get", None)):
            raise McpDiscoveryError("mcp_registry_required")
        if not callable(getattr(manifest_source, "load", None)) or not callable(
            getattr(gateway, "invoke", None)
        ):
            raise McpDiscoveryError("mcp_runtime_required")
        if not 1 <= max_arguments_bytes <= 1024 * 1024:
            raise McpDiscoveryError("mcp_arguments_cap_invalid")
        if not 1 <= max_response_bytes <= 1024 * 1024:
            raise McpDiscoveryError("mcp_response_cap_invalid")
        if not 1 <= max_tools <= 256:
            raise McpDiscoveryError("mcp_tool_cap_invalid")
        self._store = store
        self._manifest_source = manifest_source
        self._gateway = gateway
        self._max_arguments_bytes = int(max_arguments_bytes)
        self._max_response_bytes = int(max_response_bytes)
        self._max_tools = int(max_tools)

    def _open(self) -> None:
        try:
            self._ensure_open()
        except Exception:
            raise McpDiscoveryError("mcp_discovery_closed") from None

    def _declarations(
        self, manifest: Mapping[str, Any]
    ) -> dict[str, dict[str, _Declaration]]:
        raw_servers = manifest.get("mcp_servers", [])
        if not isinstance(raw_servers, (list, tuple)) or len(raw_servers) > 256:
            raise McpDiscoveryError("mcp_manifest_conflict")
        servers: dict[str, dict[str, _Declaration]] = {}
        for raw_server in raw_servers:
            if not isinstance(raw_server, Mapping) or set(raw_server) != {
                "server_id", "tools"
            }:
                raise McpDiscoveryError("mcp_manifest_conflict")
            server_id = _text(
                raw_server.get("server_id"), code="mcp_manifest_conflict"
            )
            if server_id in servers:
                raise McpDiscoveryError("mcp_manifest_conflict")
            raw_tools = raw_server.get("tools")
            if not isinstance(raw_tools, (list, tuple)) or len(raw_tools) > self._max_tools:
                raise McpDiscoveryError("mcp_manifest_conflict")
            declarations: dict[str, _Declaration] = {}
            for raw_tool in raw_tools:
                if not isinstance(raw_tool, Mapping) or set(raw_tool) != _MANIFEST_TOOL_FIELDS:
                    raise McpDiscoveryError("mcp_manifest_conflict")
                name = _text(raw_tool["name"], code="mcp_manifest_conflict")
                version = _text(raw_tool["version"], code="mcp_manifest_conflict")
                if name in declarations:
                    raise McpDiscoveryError("mcp_manifest_conflict")
                description = _text(
                    raw_tool["description"],
                    code="mcp_manifest_conflict",
                    max_bytes=4096,
                )
                schema = raw_tool["input_schema"]
                if not isinstance(schema, Mapping) or schema.get("type") != "object":
                    raise McpDiscoveryError("mcp_manifest_conflict")
                schema = _strict_json(schema, code="mcp_manifest_conflict")
                if len(_canonical(schema, code="mcp_manifest_conflict")) > self._max_response_bytes:
                    raise McpDiscoveryError("mcp_response_cap_exceeded")
                declarations[name] = _Declaration(
                    name=name,
                    version=version,
                    description=_redact_text(description),
                    input_schema=schema,
                    input_schema_digest=_schema_digest(schema),
                )
            servers[server_id] = declarations
        return servers

    async def _registered(
        self, binding: McpBinding, server_id: str
    ) -> tuple[dict[str, McpToolDefinition], str]:
        registration_server_id = digest_bound_server_id(
            server_id, binding.artifact_digest
        )
        raw = await _call(
            self._store.get,
            **binding.registry_scope,
            server_id=registration_server_id,
            timeout=self._timeout_seconds,
            code="mcp_registry_read_failed",
        )
        if raw is None:
            return {}, empty_digest()
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise McpDiscoveryError("mcp_registration_conflict") from None
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 256 * 1024:
            raise McpDiscoveryError("mcp_registration_conflict")
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            raise McpDiscoveryError("mcp_registration_conflict") from None
        if not isinstance(items, list) or len(items) > self._max_tools:
            raise McpDiscoveryError("mcp_registration_conflict")
        if _canonical(items, code="mcp_registration_conflict").decode("utf-8") != raw:
            raise McpDiscoveryError("mcp_registration_conflict")
        registered: dict[str, McpToolDefinition] = {}
        for item in items:
            if not isinstance(item, Mapping) or set(item) != _DEFINITION_FIELDS:
                raise McpDiscoveryError("mcp_registration_conflict")
            try:
                definition = McpToolDefinition(
                    tool_name=item["tool_name"],
                    tool_version=item["tool_version"],
                    capability=CapabilityName(item["capability"]),
                    operation=item["operation"],
                    input_schema_digest=item["input_schema_digest"],
                )
            except Exception:
                raise McpDiscoveryError("mcp_registration_conflict") from None
            _require_backend_definition(definition)
            if definition.tool_name in registered:
                raise McpDiscoveryError("mcp_registration_conflict")
            registered[definition.tool_name] = definition
        digest = canonical_digest({
            **binding.registry_scope,
            "server_id": registration_server_id,
            "definitions": raw,
        })
        return registered, digest

    async def discover(self, request: McpDiscoverRequest) -> McpDiscoveryResult:
        self._open()
        if not isinstance(request, McpDiscoverRequest):
            raise McpDiscoveryError("mcp_typed_request_required")
        binding = McpBinding.from_claims(request.claims)
        signed = await _call(
            self._manifest_source.load,
            claims=request.claims,
            timeout=self._timeout_seconds,
            code="mcp_manifest_read_failed",
        )
        if (
            not isinstance(signed, SignedMcpManifest)
            or signed.binding != binding
            or not isinstance(signed.manifest, Mapping)
        ):
            raise McpDiscoveryError("mcp_binding_denied")
        declarations = self._declarations(signed.manifest).get(request.server_id, {})
        registered, registration_digest = await self._registered(binding, request.server_id)
        return self._discovery_result(
            binding=binding,
            server_id=request.server_id,
            declarations=declarations,
            registered=registered,
            registration_digest=registration_digest,
            manifest=signed.manifest,
        )

    def _discovery_result(
        self, *, binding: McpBinding, server_id: str,
        declarations: Mapping[str, _Declaration],
        registered: Mapping[str, McpToolDefinition], registration_digest: str,
        manifest: Mapping[str, Any],
    ) -> McpDiscoveryResult:
        tools: list[McpTool] = []
        for name, declaration in declarations.items():
            definition = registered.get(name)
            if definition is None:
                continue
            _require_backend_definition(definition)
            if (
                definition.tool_version != declaration.version
                or definition.input_schema_digest != declaration.input_schema_digest
            ):
                raise McpDiscoveryError("mcp_registration_conflict")
            try:
                resolve_signed_rpc_operation(
                    # The same verified immutable manifest is used on discover
                    # and invoke; no RPC path is inherited from Redis.
                    manifest,
                    server_id=server_id,
                    tool_name=name,
                )
            except SignedRpcOperationError as exc:
                raise McpDiscoveryError(exc.code) from None
            tools.append(McpTool(
                server_id=server_id,
                name=name,
                version=declaration.version,
                description=declaration.description,
                input_schema=declaration.input_schema,
                capability=CapabilityName.MCP.value,
                operation="mcp.invoke",
                input_schema_digest=definition.input_schema_digest,
                artifact_digest=binding.artifact_digest,
                registration_digest=registration_digest,
            ))
        wire = [{
            "name": tool.name, "version": tool.version,
            "description": tool.description, "inputSchema": tool.input_schema,
        } for tool in tools]
        response_bytes = len(_canonical(wire, code="mcp_manifest_conflict"))
        if response_bytes > self._max_response_bytes:
            raise McpDiscoveryError("mcp_response_cap_exceeded")
        return McpDiscoveryResult(
            binding=binding, server_id=server_id,
            registration_digest=registration_digest, tools=tuple(tools),
            response_bytes=response_bytes,
        )

    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResult:
        self._open()
        if not isinstance(request, McpInvokeRequest):
            raise McpDiscoveryError("mcp_typed_request_required")
        arguments = _strict_json(request.arguments, code="mcp_arguments_invalid")
        arguments_bytes = len(_canonical(arguments, code="mcp_arguments_invalid"))
        if arguments_bytes > self._max_arguments_bytes:
            raise McpDiscoveryError("mcp_arguments_cap_exceeded")
        binding = McpBinding.from_claims(request.claims)
        # Re-read both sources for every call: no stale tool/cache survives a
        # revoke, artifact rollout, registration conflict or tenant switch.
        signed = await _call(
            self._manifest_source.load,
            claims=request.claims,
            timeout=self._timeout_seconds,
            code="mcp_manifest_read_failed",
        )
        if (
            not isinstance(signed, SignedMcpManifest)
            or signed.binding != binding
            or not isinstance(signed.manifest, Mapping)
        ):
            raise McpDiscoveryError("mcp_binding_denied")
        declarations = self._declarations(signed.manifest).get(request.server_id, {})
        registered, registration_digest = await self._registered(binding, request.server_id)
        discovered = self._discovery_result(
            binding=binding, server_id=request.server_id, declarations=declarations,
            registered=registered, registration_digest=registration_digest,
            manifest=signed.manifest,
        )
        selected = next(
            (tool for tool in discovered.tools if tool.name == request.tool_name), None
        )
        if selected is None:
            raise McpDiscoveryError("mcp_tool_unknown")
        typed_request = _typed_gateway_request(
            operation=selected.operation,
            arguments=arguments,
            claims=request.claims,
            server_id=request.server_id,
            tool_name=request.tool_name,
        )
        decision = await _call(
            self._gateway.invoke,
            claims=request.claims,
            capability=CapabilityName.MCP.value,
            operation="mcp.invoke",
            request=typed_request,
            timeout=self._timeout_seconds,
            code="mcp_gateway_failed",
        )
        if getattr(decision, "allowed", False) is not True:
            raise McpDiscoveryError("mcp_gateway_denied")
        response = getattr(decision, "data", None)
        if not isinstance(response, CapabilityMcpInvokeResponse):
            raise McpDiscoveryError("mcp_gateway_response_invalid")
        try:
            parsed = _strict_json(
                json.loads(response.response_json),
                code="mcp_gateway_response_invalid",
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise McpDiscoveryError("mcp_gateway_response_invalid") from None
        projected = _project_response(parsed)
        safe_response = _strict_json(projected, code="mcp_gateway_response_invalid")
        response_bytes = len(_canonical(
            safe_response, code="mcp_gateway_response_invalid"
        ))
        if response_bytes > self._max_response_bytes:
            raise McpDiscoveryError("mcp_response_cap_exceeded")
        return McpInvokeResult(
            binding=binding,
            server_id=request.server_id,
            tool_name=request.tool_name,
            result=safe_response,
            result_digest=canonical_digest({
                "artifact_digest": binding.artifact_digest,
                "registration_digest": registration_digest,
                "server_id": request.server_id,
                "tool_name": request.tool_name,
                "result": safe_response,
            }),
            usage=McpUsage(
                arguments_bytes=arguments_bytes,
                response_bytes=response_bytes,
            ),
        )


def _typed_gateway_request(
    *,
    operation: str,
    arguments: Mapping[str, Any],
    claims: Any,
    server_id: str,
    tool_name: str,
) -> Any:
    """Build only the typed MCP broker request; infra remapping is forbidden."""
    if operation != "mcp.invoke":
        raise McpDiscoveryError("mcp_privilege_remap_denied")
    try:
        from prodavan.application.plugins.transport import bodies, bridge

        arguments_json = _canonical(
            arguments, code="mcp_arguments_invalid"
        ).decode("utf-8")
        body = bodies.McpInvokeBody(
            server_id=server_id,
            tool_name=tool_name,
            arguments_json=arguments_json,
            arguments_digest="sha256:" + hashlib.sha256(
                arguments_json.encode("utf-8")
            ).hexdigest(),
        )
        return bridge._mcp_invoke(body, claims)
    except McpDiscoveryError:
        raise
    except Exception:
        raise McpDiscoveryError("mcp_arguments_invalid") from None


def _project_response(response: Any) -> Any:
    def project(value: Any, field_name: str = "") -> Any:
        if field_name.lower().replace("-", "_") in _SENSITIVE_FIELDS:
            return "[REDACTED]"
        if isinstance(value, Enum):
            return value.value
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: project(getattr(value, field.name), field.name)
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Mapping):
            return {
                key: project(child, key) if isinstance(key, str) else child
                for key, child in value.items()
            }
        if isinstance(value, (set, frozenset)):
            items = [project(child) for child in value]
            return sorted(
                items,
                key=lambda item: _canonical(
                    item, code="mcp_gateway_response_invalid"
                ),
            )
        if isinstance(value, (list, tuple)):
            return [project(child) for child in value]
        if isinstance(value, str):
            return _redact_text(value)
        return value

    return project(response)


__all__ = [
    "ArtifactGrantMcpManifestSource",
    "DurableMcpDiscovery",
    "McpBinding",
    "McpCapabilityGateway",
    "McpDiscoverRequest",
    "McpDiscoveryError",
    "McpDiscoveryResult",
    "McpInvokeRequest",
    "McpInvokeResult",
    "McpManifestSource",
    "McpRegistrationReader",
    "McpTool",
    "McpUsage",
    "SignedMcpManifest",
]
