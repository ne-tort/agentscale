"""Durable downstream MCP discovery tests; intentionally not run during freeze."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    canonical_digest,
)
from prodavan.application.plugins.capabilities.adapters.mcp import (
    DurableMcpAdapter,
    digest_bound_server_id,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    CapabilityName,
    McpInvokeRequest as CapabilityMcpInvokeRequest,
    McpInvokeResponse,
    McpRegisterRequest,
    McpToolDefinition,
    empty_digest,
)
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.mcp.discovery import (
    ArtifactGrantMcpManifestSource,
    DurableMcpDiscovery,
    McpDiscoverRequest,
    McpDiscoveryError,
    McpInvokeRequest,
)
from prodavan.application.plugins.wiring.adapters import (
    aclose_adapter_registry,
    attach_mcp_discovery,
)

pytestmark = pytest.mark.asyncio

DIGEST_ONE = "sha256:" + "1" * 64
DIGEST_TWO = "sha256:" + "2" * 64
SCHEMA = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
    "additionalProperties": False,
}


def _schema_digest(schema: dict[str, Any]) -> str:
    return canonical_digest(schema)


def _definition(
    name: str, *, version: str = "1.0.0", schema: dict[str, Any] | None = None
) -> McpToolDefinition:
    return McpToolDefinition(
        tool_name=name,
        tool_version=version,
        capability=CapabilityName.MCP,
        operation="mcp.invoke",
        input_schema_digest=_schema_digest(schema or SCHEMA),
    )


def _manifest_tool(definition: McpToolDefinition, *, description: str | None = None) -> dict[str, Any]:
    return {
        "name": definition.tool_name,
        "version": definition.tool_version,
        "description": description or "Signed cache reader",
        "input_schema": dict(SCHEMA),
    }


def _manifest(*tools: McpToolDefinition) -> dict[str, Any]:
    return {
        "plugin_id": "mod_x",
        "version": "1.0.0",
        "mcp_servers": [{
            "server_id": "srv",
            "tools": [_manifest_tool(tool) for tool in tools],
        }],
        "rpc_operations": [
            {
                "capability": {"id": "srv", "version": "1.0"},
                "operation": tool.tool_name,
                "http_method": "GET",
                "backend_path": f"/api/{tool.tool_name}",
                "public": False,
            }
            for tool in tools
        ],
    }


@dataclass(frozen=True, slots=True)
class FakeClaims:
    company_id: str = "co_1"
    plugin_id: str = "mod_x"
    plugin_instance_id: str = "inst_1"
    artifact_digest: str = DIGEST_ONE
    manifest_version: str = "1.0.0"
    grant_id: str = "grant_1"
    sub: str = ""
    scopes: frozenset[str] = frozenset({"mcp"})
    exp: int = 2_000_000_000
    cabinet_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not self.sub:
            object.__setattr__(
                self, "sub",
                f"plugin:{self.plugin_id}:{self.company_id}:{self.plugin_instance_id}",
            )


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str, str], str] = {}
        self.reads: list[tuple[str, str, str, str]] = []
        self.close_calls = 0

    @staticmethod
    def key(company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str) -> tuple[str, str, str, str]:
        return company_id, plugin_id, plugin_instance_id, server_id

    async def get(self, *, company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str) -> str | None:
        key = self.key(company_id, plugin_id, plugin_instance_id, server_id)
        self.reads.append(key)
        return self.values.get(key)

    async def put_if_absent(self, *, company_id: str, plugin_id: str, plugin_instance_id: str, server_id: str, definitions: str) -> bool:
        key = self.key(company_id, plugin_id, plugin_instance_id, server_id)
        if key in self.values:
            return False
        self.values[key] = definitions
        return True

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeGrantStore:
    def __init__(self, record: Any) -> None:
        self.records = {record.grant_id: record}

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return self.records.get(grant_id)


class FakeArtifacts:
    def __init__(self, *records: Any) -> None:
        self.records = {(item.plugin_id, item.version): item for item in records}

    async def get_async(self, plugin_id: str, version: str) -> Any | None:
        return self.records.get((plugin_id, version))


def _mcp_response(payload: Any) -> McpInvokeResponse:
    response_json = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return McpInvokeResponse(
        request_id="gateway-request", ok=True, digest=empty_digest(),
        server_id="srv", tool_name="read_cache", operation="read_cache",
        backend_path="/api/read_cache", status_code=200,
        arguments_bytes=0, response_bytes=len(response_json.encode("utf-8")),
        response_json=response_json,
    )


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: McpInvokeResponse | None = None
        self.error: Exception | None = None
        self.allowed = True

    async def invoke(self, *, claims: Any, capability: str, operation: str, request: Any) -> Any:
        self.calls.append({
            "claims": claims, "capability": capability,
            "operation": operation, "request": request,
        })
        if self.error is not None:
            raise self.error
        response = self.response or _mcp_response({"found": True})
        return SimpleNamespace(allowed=self.allowed, data=response)


def _grant(claims: FakeClaims) -> Any:
    return SimpleNamespace(
        grant_id=claims.grant_id,
        company_id=claims.company_id,
        plugin_id=claims.plugin_id,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        manifest_version=claims.manifest_version,
        status=GrantStatus.ACTIVE,
        is_active=True,
        revoked_at=None,
    )


def _artifact(claims: FakeClaims, manifest: dict[str, Any]) -> Any:
    return SimpleNamespace(
        plugin_id=claims.plugin_id,
        version=claims.manifest_version,
        digest=claims.artifact_digest,
        published=True,
        verification=object(),
        manifest=manifest,
    )


class Runtime:
    def __init__(self, claims: FakeClaims, registry: FakeRegistry, grants: FakeGrantStore, artifacts: FakeArtifacts, gateway: FakeGateway, consumer: DurableMcpDiscovery, registration_digest: str) -> None:
        self.claims = claims
        self.registry = registry
        self.grants = grants
        self.artifacts = artifacts
        self.gateway = gateway
        self.consumer = consumer
        self.registration_digest = registration_digest


async def _runtime(
    *, max_arguments_bytes: int = 64 * 1024,
    max_response_bytes: int = 64 * 1024,
    owns_store: bool = False,
) -> Runtime:
    claims = FakeClaims()
    registry = FakeRegistry()
    registered = (_definition("read_cache"), _definition("synthetic"))
    envelope = CapabilityEnvelope(
        request_id="register-request",
        company_id=claims.company_id,
        subject_id=claims.sub,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        grant_id=claims.grant_id,
    )
    registration = await DurableMcpAdapter(store=registry).register(
        McpRegisterRequest(server_id="srv", tools=registered, envelope=envelope)
    )
    manifest = _manifest(_definition("read_cache"), _definition("not_registered"))
    grant_store = FakeGrantStore(_grant(claims))
    artifacts = FakeArtifacts(_artifact(claims, manifest))
    gateway = FakeGateway()
    source = ArtifactGrantMcpManifestSource(
        grant_store=grant_store, artifact_registry=artifacts
    )
    consumer = DurableMcpDiscovery(
        store=registry,
        manifest_source=source,
        gateway=gateway,
        max_arguments_bytes=max_arguments_bytes,
        max_response_bytes=max_response_bytes,
        owns_store=owns_store,
    )
    return Runtime(
        claims, registry, grant_store, artifacts, gateway, consumer, registration.digest
    )


def _invoke(runtime: Runtime, *, name: str = "read_cache", arguments: dict[str, Any] | None = None, claims: FakeClaims | None = None) -> McpInvokeRequest:
    return McpInvokeRequest(
        server_id="srv", tool_name=name,
        arguments={"key": "alpha"} if arguments is None else arguments,
        claims=claims or runtime.claims,
    )


async def test_registration_rejects_cross_capability_before_registry_write() -> None:
    runtime = await _runtime()
    forged = McpToolDefinition(
        tool_name="database",
        tool_version="1.0.0",
        capability=CapabilityName.DOCS,
        operation="docs.query",
        input_schema_digest=_schema_digest(SCHEMA),
    )
    envelope = CapabilityEnvelope(
        request_id="forged-request", company_id=runtime.claims.company_id,
        subject_id=runtime.claims.sub,
        plugin_instance_id=runtime.claims.plugin_instance_id,
        artifact_digest=runtime.claims.artifact_digest,
        grant_id=runtime.claims.grant_id,
    )
    with pytest.raises(CapabilityAccessDenied, match="mcp_privilege_remap_denied"):
        await DurableMcpAdapter(store=runtime.registry).register(
            McpRegisterRequest(server_id="srv", tools=(forged,), envelope=envelope)
        )
    assert len(runtime.registry.values) == 1


async def test_discover_only_declared_and_registered_with_signed_metadata() -> None:
    runtime = await _runtime()
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="srv", claims=runtime.claims)
    )
    assert [tool.name for tool in result.tools] == ["read_cache"]
    tool = result.tools[0]
    assert tool.version == "1.0.0"
    assert tool.description == "Signed cache reader"
    assert tool.input_schema == SCHEMA
    assert tool.artifact_digest == DIGEST_ONE
    assert tool.registration_digest == runtime.registration_digest
    assert result.registration_digest == runtime.registration_digest
    assert runtime.gateway.calls == []


async def test_discovery_detects_registered_manifest_version_conflict() -> None:
    runtime = await _runtime()
    artifact = runtime.artifacts.records[("mod_x", "1.0.0")]
    artifact.manifest["mcp_servers"][0]["tools"][0]["version"] = "2.0.0"
    with pytest.raises(McpDiscoveryError, match="mcp_registration_conflict"):
        await runtime.consumer.discover(
            McpDiscoverRequest(server_id="srv", claims=runtime.claims)
        )


async def test_invoke_typed_allowed_and_unknown_operation_denied() -> None:
    runtime = await _runtime()
    result = await runtime.consumer.invoke(_invoke(runtime))
    assert result.result["found"] is True
    assert result.usage.arguments_bytes == len(b'{"key":"alpha"}')
    assert result.usage.response_bytes == len(json.dumps(
        result.result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    assert len(runtime.gateway.calls) == 1
    call = runtime.gateway.calls[0]
    assert call["capability"] == "mcp"
    assert call["operation"] == "mcp.invoke"
    assert isinstance(call["request"], CapabilityMcpInvokeRequest)
    assert call["request"].server_id == "srv"
    assert call["request"].tool_name == "read_cache"
    assert json.loads(call["request"].arguments_json) == {"key": "alpha"}
    assert call["request"].envelope.artifact_digest == DIGEST_ONE

    with pytest.raises(McpDiscoveryError, match="mcp_tool_unknown"):
        await runtime.consumer.invoke(_invoke(runtime, name="synthetic"))
    assert len(runtime.gateway.calls) == 1

    # A corrupted registration cannot introduce an arbitrary gateway operation.
    key = FakeRegistry.key(
        "co_1", "mod_x", "inst_1", digest_bound_server_id("srv", DIGEST_ONE)
    )
    registered = json.loads(runtime.registry.values[key])
    registered[0]["capability"] = "docs"
    registered[0]["operation"] = "docs.query"
    runtime.registry.values[key] = json.dumps(
        registered, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with pytest.raises(McpDiscoveryError, match="mcp_privilege_remap_denied"):
        await runtime.consumer.discover(
            McpDiscoverRequest(server_id="srv", claims=runtime.claims)
        )
    with pytest.raises(McpDiscoveryError, match="mcp_privilege_remap_denied"):
        await runtime.consumer.invoke(_invoke(runtime))
    assert len(runtime.gateway.calls) == 1


async def test_revoke_denies_discovery_and_invoke_without_gateway_call() -> None:
    runtime = await _runtime()
    record = runtime.grants.records["grant_1"]
    record.status = GrantStatus.REVOKED
    record.is_active = False
    with pytest.raises(McpDiscoveryError, match="mcp_binding_denied"):
        await runtime.consumer.discover(
            McpDiscoverRequest(server_id="srv", claims=runtime.claims)
        )
    with pytest.raises(McpDiscoveryError, match="mcp_binding_denied"):
        await runtime.consumer.invoke(_invoke(runtime))
    assert runtime.gateway.calls == []


async def test_digest_update_removes_old_tools_and_stale_claims_denied() -> None:
    runtime = await _runtime()
    new_claims = replace(
        runtime.claims, artifact_digest=DIGEST_TWO, manifest_version="2.0.0"
    )
    record = runtime.grants.records["grant_1"]
    record.artifact_digest = DIGEST_TWO
    record.manifest_version = "2.0.0"
    # Even an identical declaration requires registration in the new digest scope.
    updated_manifest = _manifest(_definition("read_cache"))
    updated_manifest["version"] = "2.0.0"
    runtime.artifacts.records[("mod_x", "2.0.0")] = _artifact(
        new_claims, updated_manifest
    )

    with pytest.raises(McpDiscoveryError, match="mcp_binding_denied"):
        await runtime.consumer.invoke(_invoke(runtime))
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="srv", claims=new_claims)
    )
    assert result.tools == ()
    with pytest.raises(McpDiscoveryError, match="mcp_tool_unknown"):
        await runtime.consumer.invoke(_invoke(runtime, claims=new_claims))
    assert runtime.gateway.calls == []


async def test_tenant_isolation_reads_only_exact_registry_scope() -> None:
    runtime = await _runtime()
    other = replace(runtime.claims, company_id="co_2", grant_id="grant_2", sub="")
    runtime.grants.records["grant_2"] = _grant(other)
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="srv", claims=other)
    )
    assert result.tools == ()
    assert runtime.registry.reads[-1] == (
        "co_2", "mod_x", "inst_1", digest_bound_server_id("srv", DIGEST_ONE)
    )
    with pytest.raises(McpDiscoveryError, match="mcp_tool_unknown"):
        await runtime.consumer.invoke(_invoke(runtime, claims=other))
    assert runtime.gateway.calls == []


async def test_arguments_and_response_caps_are_enforced() -> None:
    small_args = await _runtime(max_arguments_bytes=8)
    with pytest.raises(McpDiscoveryError, match="mcp_arguments_cap_exceeded"):
        await small_args.consumer.invoke(_invoke(small_args))
    assert small_args.gateway.calls == []

    small_response = await _runtime(max_response_bytes=512)
    small_response.gateway.response = _mcp_response({"value": "x" * 1024})
    with pytest.raises(McpDiscoveryError, match="mcp_response_cap_exceeded"):
        await small_response.consumer.invoke(_invoke(small_response))


async def test_plaintext_secrets_and_dsns_are_redacted_and_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    runtime = await _runtime()
    secret = "plaintext-token"
    dsn = "redis://user:plaintext-password@private-host"
    runtime.gateway.response = _mcp_response({"token": secret, "note": dsn})
    result = await runtime.consumer.invoke(_invoke(runtime))
    assert result.result["token"] == "[REDACTED]"
    assert result.result["note"] == "[REDACTED_DSN]"
    assert secret not in json.dumps(result.result)
    assert dsn not in json.dumps(result.result)

    runtime.gateway.error = RuntimeError(dsn)
    with pytest.raises(McpDiscoveryError, match="mcp_gateway_failed") as caught:
        await runtime.consumer.invoke(_invoke(runtime))
    assert dsn not in str(caught.value)
    assert secret not in caplog.text
    assert dsn not in caplog.text


async def test_legacy_registration_without_digest_scope_is_not_discovered() -> None:
    runtime = await _runtime()
    scoped = digest_bound_server_id("srv", DIGEST_ONE)
    key = FakeRegistry.key("co_1", "mod_x", "inst_1", scoped)
    legacy = runtime.registry.values.pop(key)
    runtime.registry.values[FakeRegistry.key("co_1", "mod_x", "inst_1", "srv")] = legacy
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="srv", claims=runtime.claims)
    )
    assert result.tools == ()


async def test_close_once_through_adapter_registry_lifecycle() -> None:
    runtime = await _runtime(owns_store=True)
    adapters = AdapterRegistry()
    attach_mcp_discovery(adapters, runtime.consumer)
    await aclose_adapter_registry(adapters)
    await aclose_adapter_registry(adapters)
    await runtime.consumer.aclose()
    assert runtime.registry.close_calls == 1
    with pytest.raises(McpDiscoveryError, match="mcp_discovery_closed"):
        await runtime.consumer.discover(
            McpDiscoverRequest(server_id="srv", claims=runtime.claims)
        )
