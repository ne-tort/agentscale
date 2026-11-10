"""Digest-scoped hello MCP registration and backend broker tests; not run in freeze."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.capability_gateway_service import DecisionCode
from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied as McpAccessDenied,
    CapabilityBackendError as McpBackendError,
    canonical_digest,
)
from prodavan.application.plugins.capabilities.adapters.mcp import (
    DurableMcpAdapter,
    digest_bound_server_id,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    CapabilityName,
    McpInvokeRequest,
    McpRegisterRequest,
    McpToolDefinition,
)
from prodavan.application.plugins.mcp.broker import DurableMcpBroker
from prodavan.application.plugins.mcp.discovery import (
    ArtifactGrantMcpManifestSource,
    DurableMcpDiscovery,
    McpBinding,
    McpDiscoverRequest,
    McpDiscoveryError,
    McpInvokeRequest as DownstreamMcpInvokeRequest,
)
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    PluginQuota,
    QuotaDecision,
    QuotaOp,
    QuotaReason,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService
from prodavan.application.plugins.gateway.errors import GatewayError, GatewayErrorCode
from prodavan.application.plugins.gateway.headers import (
    HEADER_ARTIFACT_DIGEST,
    HEADER_CAPABILITY_TOKEN,
    HEADER_COMPANY_ID,
    HEADER_PLUGIN_ID,
    HEADER_PLUGIN_INSTANCE_ID,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
    GatewayAuditEvent,
    WakeLease,
)
from prodavan.application.plugins.gateway.proxy import GatewayProxy
from prodavan.application.plugins.identity.revocation import InMemoryRevocation
from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
    BearerAuthorizationInjector,
)

pytestmark = pytest.mark.asyncio

DIGEST_ONE = "sha256:" + "1" * 64
DIGEST_TWO = "sha256:" + "2" * 64
LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "company_id": {"type": "string", "minLength": 1},
        "cabinet_id": {"type": "string", "minLength": 1},
        "project_id": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
    "required": ["company_id"],
    "additionalProperties": False,
}
GREET_SCHEMA = {
    "type": "object",
    "properties": {
        "company_id": {"type": "string", "minLength": 1},
        "cabinet_id": {"type": "string", "minLength": 1},
        "project_id": {"type": "string", "minLength": 1},
        "message": {"type": "string", "minLength": 1, "maxLength": 4000},
    },
    "required": ["company_id", "message"],
    "additionalProperties": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _schema_digest(schema: dict[str, Any]) -> str:
    return canonical_digest(schema)


def _definition(name: str, schema: dict[str, Any]) -> McpToolDefinition:
    return McpToolDefinition(
        tool_name=name,
        tool_version="1.0.0",
        capability=CapabilityName.MCP,
        operation="mcp.invoke",
        input_schema_digest=_schema_digest(schema),
    )


def _manifest_tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Hello {name} operation.",
        "version": "1.0.0",
        "input_schema": schema,
    }


def _manifest(*, rpc: bool = True, extra: str | None = None) -> dict[str, Any]:
    tools = [_manifest_tool("list", LIST_SCHEMA), _manifest_tool("greet", GREET_SCHEMA)]
    if extra:
        tools.append(_manifest_tool(extra, LIST_SCHEMA))
    operations: list[dict[str, Any]] = []
    if rpc:
        operations.extend([
            {
                "capability": {"id": "hello.greetings", "version": "1.0"},
                "operation": "list",
                "http_method": "GET",
                "backend_path": "/api/greetings/list",
                "public": False,
            },
            {
                "capability": {"id": "hello.greetings", "version": "1.0"},
                "operation": "greet",
                "http_method": "POST",
                "backend_path": "/api/greetings",
                "public": False,
            },
        ])
    return {
        "plugin_id": "hello_plugin",
        "version": "0.1.0",
        "mcp_servers": [{
            "server_id": "hello.greetings",
            "tools": tools,
        }],
        "rpc_operations": operations,
    }


@dataclass(frozen=True, slots=True)
class Claims:
    company_id: str = "co_1"
    plugin_id: str = "hello_plugin"
    plugin_instance_id: str = "inst_1"
    artifact_digest: str = DIGEST_ONE
    manifest_version: str = "0.1.0"
    grant_id: str = "grant_1"
    scopes: frozenset[str] = frozenset({"mcp"})
    exp: int = 2_000_000_000
    cabinet_id: str | None = None
    project_id: str | None = None

    @property
    def sub(self) -> str:
        return f"plugin:{self.plugin_id}:{self.company_id}:{self.plugin_instance_id}"


class Registry:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str, str], str] = {}
        self.close_calls = 0

    async def get(self, **scope: str) -> str | None:
        return self.values.get((scope["company_id"], scope["plugin_id"], scope["plugin_instance_id"], scope["server_id"]))

    async def put_if_absent(self, **kwargs: Any) -> bool:
        key = (kwargs["company_id"], kwargs["plugin_id"], kwargs["plugin_instance_id"], kwargs["server_id"])
        if key in self.values:
            return False
        self.values[key] = kwargs["definitions"]
        return True

    async def aclose(self) -> None:
        self.close_calls += 1


class Grants:
    def __init__(self, record: Any) -> None:
        self.records = {record.grant_id: record}

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return self.records.get(grant_id)


class Artifacts:
    def __init__(self, *items: Any) -> None:
        self.records = {(item.plugin_id, item.version): item for item in items}

    async def get_async(self, plugin_id: str, version: str) -> Any | None:
        return self.records.get((plugin_id, version))


class BackendProxy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def forward(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(status_code=self.status_code, body=self.body, stream=None)

    def response(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


BackendProxy.status_code = 200
BackendProxy.body = b"[]"


class UnknownFailureProxy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def forward(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise RuntimeError("sensitive backend detail")


class StrictActivation:
    """Exact activation port: every wake lease is grant/instance/digest bound."""

    def __init__(
        self,
        claims: Claims,
        *,
        lease_digest: str | None = None,
        omit_lease_digest: bool = False,
    ) -> None:
        self.claims = claims
        self.lease_digest = (
            None
            if omit_lease_digest
            else lease_digest or claims.artifact_digest
        )
        self.wake_calls: list[dict[str, str | None]] = []
        self.leases: list[WakeLease] = []
        self.ready_calls: list[tuple[WakeLease, float]] = []

    async def wake(
        self,
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        call = {
            "plugin_id": plugin_id,
            "company_id": company_id,
            "plugin_instance_id": plugin_instance_id,
        }
        self.wake_calls.append(call)
        assert call == {
            "plugin_id": self.claims.plugin_id,
            "company_id": self.claims.company_id,
            "plugin_instance_id": self.claims.plugin_instance_id,
        }
        lease = WakeLease(
            plugin_id=plugin_id,
            company_id=company_id,
            capability_token=f"fresh-wake-token-{len(self.leases) + 1}",
            expires_at=2_000_000_000.0,
            ready=True,
            plugin_instance_id=plugin_instance_id,
            artifact_digest=self.lease_digest,
            grant_id=self.claims.grant_id,
        )
        self.leases.append(lease)
        return lease

    async def wait_ready(
        self, lease: WakeLease, *, timeout_seconds: float
    ) -> bool:
        assert self.leases and lease is self.leases[-1]
        self.ready_calls.append((lease, timeout_seconds))
        return lease.ready


class StrictBackendHttp:
    def __init__(self) -> None:
        self.requests: list[BackendRequest] = []
        self.status_code = 200
        self.body = b"[]"

    def response(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = _canonical(body).encode("utf-8")

    async def send(self, request: BackendRequest) -> BackendResponse:
        self.requests.append(request)
        return BackendResponse(status_code=self.status_code, body=self.body)


class StrictGatewayAudit:
    def __init__(self) -> None:
        self.events: list[GatewayAuditEvent] = []

    async def record(self, event: GatewayAuditEvent) -> None:
        self.events.append(event)


class StrictGateway:
    """Use the production proxy; only activation/transport/audit are recording ports."""

    def __init__(
        self,
        claims: Claims,
        *,
        lease_digest: str | None = None,
        omit_lease_digest: bool = False,
    ) -> None:
        self.activation = StrictActivation(
            claims,
            lease_digest=lease_digest,
            omit_lease_digest=omit_lease_digest,
        )
        self.backend = StrictBackendHttp()
        self.audit = StrictGatewayAudit()
        self.gateway = GatewayProxy(
            activation=self.activation,
            backend_http=BearerAuthorizationInjector(self.backend),
            audit=self.audit,
        )
        self.forward_calls: list[dict[str, Any]] = []

    def response(self, status_code: int, body: Any) -> None:
        self.backend.response(status_code, body)

    async def forward(self, **kwargs: Any) -> BackendResponse:
        self.forward_calls.append(kwargs)
        return await self.gateway.forward(**kwargs)


class AdapterGateway:
    """Simulates the service's typed dispatch without inventing another fake backend."""

    def __init__(self, adapter: DurableMcpAdapter) -> None:
        self.adapter = adapter

    async def invoke(self, *, claims: Any, capability: str, operation: str, request: Any) -> Any:
        if capability != "mcp" or operation not in {"invoke", "mcp.invoke"}:
            return SimpleNamespace(allowed=False, reason="only the typed MCP broker path is wired")
        return SimpleNamespace(allowed=True, data=await self.adapter.invoke(request))


class CountingQuota:
    def __init__(self) -> None:
        self.calls: list[tuple[QuotaOp, int, str]] = []

    async def enforce(
        self, key: Any, op: QuotaOp, amount: int, limits: PluginQuota
    ) -> QuotaDecision:
        limit = limits.limit_for(op)
        self.calls.append((op, amount, key.cache_key()))
        return QuotaDecision(
            allowed=True,
            code=QuotaReason.ALLOWED,
            key=key,
            op=op,
            amount=amount,
            limit=limit,
            used=0,
            remaining=max(limit - amount, 0),
        )


def _grant(claims: Claims) -> Any:
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
        capabilities=frozenset({"mcp"}),
        quotas=PluginQuota(),
        issued_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )


def _grant_binding(claims: Claims) -> McpBinding:
    return McpBinding(
        company_id=claims.company_id,
        plugin_id=claims.plugin_id,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        manifest_version=claims.manifest_version,
        grant_id=claims.grant_id,
    )


def _invalidate_grant(record: Any, case: str) -> None:
    if case == "missing_status":
        delattr(record, "status")
    elif case == "missing_is_active":
        delattr(record, "is_active")
    elif case == "false_is_active":
        record.is_active = False
    elif case == "revoked":
        record.status = GrantStatus.REVOKED
        record.is_active = False
        record.revoked_at = datetime.now(UTC)
    elif case == "expired":
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif case == "unknown_status":
        record.status = "pending"
    elif case == "status_flag_mismatch":
        record.status = GrantStatus.REVOKED
        record.is_active = True
    elif case == "legacy_string_status":
        record.status = "active"
    else:
        raise AssertionError(f"unknown grant corruption: {case}")


def _artifact(claims: Claims, manifest: dict[str, Any]) -> Any:
    return SimpleNamespace(
        plugin_id=claims.plugin_id,
        version=claims.manifest_version,
        digest=claims.artifact_digest,
        published=True,
        verification=object(),
        manifest=manifest,
    )


class Runtime:
    def __init__(self, claims: Claims, registry: Registry, grants: Grants, artifacts: Artifacts, adapter: DurableMcpAdapter, consumer: DurableMcpDiscovery, proxy: Any) -> None:
        self.claims = claims
        self.registry = registry
        self.grants = grants
        self.artifacts = artifacts
        self.adapter = adapter
        self.consumer = consumer
        self.proxy = proxy

    def registration_key(self) -> tuple[str, str, str, str]:
        return (
            self.claims.company_id,
            self.claims.plugin_id,
            self.claims.plugin_instance_id,
            digest_bound_server_id("hello.greetings", self.claims.artifact_digest),
        )


async def _runtime(
    claims: Claims | None = None, *, proxy: Any | None = None
) -> Runtime:
    claims = claims or Claims()
    registry = Registry()
    adapter = DurableMcpAdapter(store=registry)
    envelope = CapabilityEnvelope(
        request_id="register-request", company_id=claims.company_id,
        subject_id=claims.sub, plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest, grant_id=claims.grant_id,
    )
    request = McpRegisterRequest(
        server_id="hello.greetings",
        tools=(
            _definition("list", LIST_SCHEMA),
            _definition("greet", GREET_SCHEMA),
            _definition("synthetic", LIST_SCHEMA),
        ),
        envelope=envelope,
    )
    await adapter.register(request)
    await adapter.register(request)
    manifest = _manifest()
    grants = Grants(_grant(claims))
    artifacts = Artifacts(_artifact(claims, manifest))
    source = ArtifactGrantMcpManifestSource(grant_store=grants, artifact_registry=artifacts)
    consumer = DurableMcpDiscovery(
        store=registry, manifest_source=source, gateway=AdapterGateway(adapter)
    )
    proxy = proxy or BackendProxy()
    broker = DurableMcpBroker(
        discovery=consumer,
        manifest_source=source,
        grant_store=grants,
        backend_proxy=proxy,
    )
    adapter.attach_broker(broker)
    return Runtime(claims, registry, grants, artifacts, adapter, consumer, proxy)


def _invoke(
    runtime: Runtime,
    *,
    tool: str = "list",
    arguments: dict[str, Any] | None = None,
    claims: Claims | None = None,
) -> McpInvokeRequest:
    arguments = {"company_id": runtime.claims.company_id, "limit": 2} if arguments is None else arguments
    payload = _canonical(arguments)
    current = claims or runtime.claims
    envelope = CapabilityEnvelope(
        request_id="invoke-request", company_id=current.company_id,
        subject_id=current.sub, plugin_instance_id=current.plugin_instance_id,
        artifact_digest=current.artifact_digest, grant_id=current.grant_id,
    )
    return McpInvokeRequest(
        server_id="hello.greetings",
        tool_name=tool,
        arguments_json=payload,
        arguments_digest="sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        manifest_version=current.manifest_version,
        envelope=envelope,
    )


async def test_registration_is_digest_scoped_and_idempotent() -> None:
    runtime = await _runtime()
    key = runtime.registration_key()
    assert key[-1] == digest_bound_server_id("hello.greetings", DIGEST_ONE)
    assert len(runtime.registry.values) == 1
    assert '"operation":"mcp.invoke"' in runtime.registry.values[key]
    changed = McpRegisterRequest(
        server_id="hello.greetings",
        tools=(_definition("changed", LIST_SCHEMA),),
        envelope=CapabilityEnvelope(
            request_id="changed", company_id=runtime.claims.company_id,
            subject_id=runtime.claims.sub,
            plugin_instance_id=runtime.claims.plugin_instance_id,
            artifact_digest=runtime.claims.artifact_digest,
            grant_id=runtime.claims.grant_id,
        ),
    )
    with pytest.raises(McpAccessDenied, match="mcp_registration_conflict"):
        await runtime.adapter.register(changed)


async def test_discovery_returns_hello_tools_but_not_unregistered_synthetic() -> None:
    runtime = await _runtime()
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="hello.greetings", claims=runtime.claims)
    )
    assert [(tool.name, tool.capability, tool.operation) for tool in result.tools] == [
        ("list", "mcp", "mcp.invoke"), ("greet", "mcp", "mcp.invoke"),
    ]
    assert runtime.adapter._broker is not None


async def test_hello_list_and_greet_execute_canonical_rpc_operations() -> None:
    runtime = await _runtime()
    runtime.proxy.response(200, [{"id": "one"}])
    listed = await runtime.adapter.invoke(_invoke(runtime, tool="list"))
    assert listed.operation == "list"
    assert listed.backend_path == "/api/greetings/list"
    assert json.loads(listed.response_json) == [{"id": "one"}]
    assert listed.arguments_bytes == len(b'{"company_id":"co_1","limit":2}')
    call = runtime.proxy.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/greetings/list"
    assert call["body"] == b""
    assert call["query_string"] == b"company_id=co_1&limit=2"
    assert call["claims"].company_id == "co_1"
    assert call["claims"].plugin_instance_id == "inst_1"

    runtime.proxy.response(201, {"id": "two", "message": "hello"})
    greeting_arguments = {"company_id": "co_1", "message": "hello"}
    greeted = await runtime.adapter.invoke(
        _invoke(runtime, tool="greet", arguments=greeting_arguments)
    )
    assert greeted.status_code == 201
    assert greeted.backend_path == "/api/greetings"
    assert json.loads(greeted.response_json)["id"] == "two"
    call = runtime.proxy.calls[-1]
    assert call["method"] == "POST"
    assert call["query_string"] == b""
    assert json.loads(call["body"]) == greeting_arguments


async def test_real_gateway_list_and_greet_use_exact_binding_and_fresh_wake_tokens() -> None:
    claims = Claims()
    strict = StrictGateway(claims)
    runtime = await _runtime(claims, proxy=strict)

    strict.response(200, [{"id": "one"}])
    listed = await runtime.adapter.invoke(_invoke(runtime, tool="list"))
    assert listed.backend_path == "/api/greetings/list"

    strict.response(201, {"id": "two", "message": "hello"})
    greeting_arguments = {"company_id": "co_1", "message": "hello"}
    greeted = await runtime.adapter.invoke(
        _invoke(runtime, tool="greet", arguments=greeting_arguments)
    )
    assert greeted.backend_path == "/api/greetings"

    assert len(strict.activation.leases) == 2
    assert len(strict.backend.requests) == 2
    assert [request.path for request in strict.backend.requests] == [
        "/api/greetings/list", "/api/greetings",
    ]
    wake_tokens = [lease.capability_token for lease in strict.activation.leases]
    assert len(set(wake_tokens)) == len(wake_tokens)

    for index, (call, lease, request) in enumerate(zip(
        strict.forward_calls, strict.activation.leases, strict.backend.requests
    ), start=1):
        gateway_claims = call["claims"]
        verified_grant = call["grant"]
        assert gateway_claims.plugin_id == "hello_plugin"
        assert gateway_claims.company_id == "co_1"
        assert gateway_claims.subject_id == claims.sub
        assert gateway_claims.scopes == frozenset({"mcp"})
        assert gateway_claims.project_id is None
        assert gateway_claims.grant_id == "grant_1"
        assert gateway_claims.anonymous is False
        assert gateway_claims.plugin_instance_id == "inst_1"
        assert gateway_claims.artifact_digest == DIGEST_ONE

        assert verified_grant.grant_id == "grant_1"
        assert verified_grant.active is True
        assert verified_grant.operations == frozenset({"mcp"})
        assert verified_grant.plugin_instance_id == "inst_1"
        assert verified_grant.artifact_digest == DIGEST_ONE

        assert lease.plugin_id == "hello_plugin"
        assert lease.company_id == "co_1"
        assert lease.plugin_instance_id == "inst_1"
        assert lease.artifact_digest == DIGEST_ONE
        assert lease.grant_id == "grant_1"
        assert lease.ready is True

        assert request.plugin_instance_id == "inst_1"
        assert request.headers[HEADER_PLUGIN_ID] == "hello_plugin"
        assert request.headers[HEADER_COMPANY_ID] == "co_1"
        assert request.headers[HEADER_PLUGIN_INSTANCE_ID] == "inst_1"
        assert request.headers[HEADER_ARTIFACT_DIGEST] == DIGEST_ONE
        assert "X-Project-ID" not in request.headers
        assert request.headers[HEADER_CAPABILITY_TOKEN] == wake_tokens[index - 1]
        assert request.headers["Authorization"] == (
            f"Bearer {wake_tokens[index - 1]}"
        )

    first, second = strict.backend.requests
    assert first.method == "GET"
    assert first.body == b""
    assert first.query_string == b"company_id=co_1&limit=2"
    assert first.headers["Accept"] == "application/json"
    assert "Content-Type" not in first.headers
    assert second.method == "POST"
    assert second.query_string == b""
    assert second.body == _canonical(greeting_arguments).encode("utf-8")
    assert second.headers["Accept"] == "application/json"
    assert second.headers["Content-Type"] == "application/json"


async def test_real_gateway_missing_or_mismatched_digest_denies_before_wake_and_backend() -> None:
    claims = Claims()
    strict = StrictGateway(claims)
    runtime = await _runtime(claims, proxy=strict)
    strict.response(200, [{"id": "one"}])
    await runtime.adapter.invoke(_invoke(runtime, tool="list"))
    base = strict.forward_calls[-1]

    cases = [
        ("claims_missing", None, DIGEST_ONE),
        ("claims_mismatch", DIGEST_TWO, DIGEST_ONE),
        ("grant_missing", DIGEST_ONE, None),
        ("grant_mismatch", DIGEST_ONE, DIGEST_TWO),
    ]
    for name, claims_digest, grant_digest in cases:
        modified = {
            **base,
            "claims": replace(base["claims"], artifact_digest=claims_digest),
            "grant": replace(base["grant"], artifact_digest=grant_digest),
        }
        with pytest.raises(GatewayError) as caught:
            await strict.forward(**modified)
        assert caught.value.gateway_code is GatewayErrorCode.PORT_UNAVAILABLE, name

    assert len(strict.activation.wake_calls) == 1
    assert len(strict.backend.requests) == 1


@pytest.mark.parametrize(("omit_lease_digest", "lease_digest"), [
    (True, None),
    (False, DIGEST_TWO),
])
async def test_real_gateway_bad_lease_digest_denies_before_backend(
    omit_lease_digest: bool, lease_digest: str | None
) -> None:
    claims = Claims()
    strict = StrictGateway(
        claims,
        omit_lease_digest=omit_lease_digest,
        lease_digest=lease_digest,
    )
    runtime = await _runtime(claims, proxy=strict)
    strict.response(200, [{"id": "one"}])

    with pytest.raises(McpBackendError) as caught:
        await runtime.adapter.invoke(_invoke(runtime, tool="list"))

    assert caught.value.code == GatewayErrorCode.WAKE_LEASE_MISSING.value
    assert str(caught.value) == GatewayErrorCode.WAKE_LEASE_MISSING.value
    assert len(strict.activation.wake_calls) == 1
    assert strict.activation.ready_calls == []
    assert strict.backend.requests == []


async def test_unknown_proxy_failure_keeps_generic_safe_mcp_error() -> None:
    proxy = UnknownFailureProxy()
    runtime = await _runtime(proxy=proxy)

    with pytest.raises(McpBackendError) as caught:
        await runtime.adapter.invoke(_invoke(runtime, tool="list"))

    assert caught.value.code == "mcp_backend_invoke_failed"
    assert str(caught.value) == "mcp_backend_invoke_failed"
    assert "sensitive backend detail" not in str(caught.value)
    assert len(proxy.calls) == 1


async def test_unknown_or_unmapped_operation_denies_without_backend_call() -> None:
    runtime = await _runtime()
    with pytest.raises(McpAccessDenied, match="mcp_tool_unknown"):
        await runtime.adapter.invoke(_invoke(runtime, tool="stats"))
    # A corrupted registration cannot remap an MCP tool onto docs.query.
    key = runtime.registration_key()
    tools = json.loads(runtime.registry.values[key])
    tools[0]["capability"] = "docs"
    tools[0]["operation"] = "docs.query"
    runtime.registry.values[key] = _canonical(tools)
    # Direct discovery keeps its typed discovery failure; the broker maps it
    # to CapabilityAccessDenied only at the capability adapter boundary below.
    with pytest.raises(McpDiscoveryError, match="mcp_privilege_remap_denied"):
        await runtime.consumer.discover(
            McpDiscoverRequest(server_id="hello.greetings", claims=runtime.claims)
        )
    with pytest.raises(McpAccessDenied, match="mcp_privilege_remap_denied"):
        await runtime.adapter.invoke(_invoke(runtime, tool="list"))

    # Restore the valid tool before independently checking an unknown RPC mapping.
    tools[0]["capability"] = "mcp"
    tools[0]["operation"] = "mcp.invoke"
    runtime.registry.values[key] = _canonical(tools)
    # Even a declared+registered tool is denied without a canonical rpc_operation.
    artifact = runtime.artifacts.records[("hello_plugin", "0.1.0")]
    artifact.manifest["mcp_servers"][0]["tools"].append(
        _manifest_tool("stats", LIST_SCHEMA)
    )
    definition = _definition("stats", LIST_SCHEMA)
    tools.append({
        "tool_name": definition.tool_name,
        "tool_version": definition.tool_version,
        "capability": definition.capability.value,
        "operation": definition.operation,
        "input_schema_digest": definition.input_schema_digest,
    })
    runtime.registry.values[key] = _canonical(tools)
    with pytest.raises(McpAccessDenied, match="mcp_rpc_operation_unknown"):
        await runtime.adapter.invoke(_invoke(runtime, tool="stats"))
    assert runtime.proxy.calls == []


async def test_tenant_argument_and_argument_digest_mismatches_deny() -> None:
    runtime = await _runtime()
    with pytest.raises(McpAccessDenied, match="mcp_tenant_assertion_mismatch"):
        await runtime.adapter.invoke(_invoke(
            runtime, arguments={"company_id": "co_other", "limit": 2}
        ))
    bad = _invoke(runtime)
    object.__setattr__(
        bad, "arguments_digest",
        "sha256:" + "0" * 64,
    )
    with pytest.raises(McpAccessDenied, match="mcp_arguments_digest_mismatch"):
        await runtime.adapter.invoke(bad)
    assert runtime.proxy.calls == []


async def test_missing_and_foreign_durable_grants_deny_before_backend() -> None:
    runtime = await _runtime()
    foreign = replace(runtime.claims, grant_id="grant_foreign")
    foreign_request = _invoke(runtime, claims=foreign)
    with pytest.raises(McpAccessDenied, match="mcp_binding_denied"):
        await runtime.adapter.invoke(foreign_request)
    assert runtime.proxy.calls == []


@pytest.mark.parametrize("case", [
    "missing_status",
    "missing_is_active",
    "false_is_active",
    "revoked",
    "expired",
    "unknown_status",
    "status_flag_mismatch",
    "legacy_string_status",
])
async def test_explicit_consistent_active_grant_is_required_before_backend(
    case: str,
) -> None:
    runtime = await _runtime()
    record = runtime.grants.records["grant_1"]
    _invalidate_grant(record, case)

    with pytest.raises(McpDiscoveryError, match="mcp_binding_denied"):
        await runtime.consumer.discover(McpDiscoverRequest(
            server_id="hello.greetings", claims=runtime.claims,
        ))

    broker = runtime.adapter._broker
    assert isinstance(broker, DurableMcpBroker)
    with pytest.raises(McpAccessDenied, match="mcp_binding_denied"):
        await broker._verified_grant(_grant_binding(runtime.claims))

    with pytest.raises(McpAccessDenied, match="mcp_binding_denied"):
        await runtime.adapter.invoke(_invoke(runtime))
    assert runtime.proxy.calls == []


async def test_revoke_and_stale_digest_deny_before_backend() -> None:
    runtime = await _runtime()
    runtime.grants.records["grant_1"].status = GrantStatus.REVOKED
    runtime.grants.records["grant_1"].is_active = False
    with pytest.raises(McpAccessDenied, match="mcp_binding_denied"):
        await runtime.adapter.invoke(_invoke(runtime))
    assert runtime.proxy.calls == []

    new_claims = replace(runtime.claims, artifact_digest=DIGEST_TWO)
    runtime.grants.records["grant_1"].status = GrantStatus.ACTIVE
    runtime.grants.records["grant_1"].is_active = True
    runtime.grants.records["grant_1"].artifact_digest = DIGEST_TWO
    runtime.artifacts.records[("hello_plugin", "0.1.0")] = _artifact(
        new_claims, _manifest()
    )
    stale = _invoke(runtime)
    with pytest.raises(McpAccessDenied, match="mcp_binding_denied"):
        await runtime.adapter.invoke(stale)

    # Identical declarations under a new digest require a new registration.
    fresh = _invoke(runtime, claims=new_claims)
    result = await runtime.consumer.discover(
        McpDiscoverRequest(server_id="hello.greetings", claims=new_claims)
    )
    assert result.tools == ()
    with pytest.raises(McpAccessDenied, match="mcp_tool_unknown"):
        await runtime.adapter.invoke(fresh)
    assert runtime.proxy.calls == []


async def test_downstream_consumer_invokes_hello_without_fake_infra_mapping() -> None:
    runtime = await _runtime()
    runtime.proxy.response(200, [{"id": "downstream"}])
    result = await runtime.consumer.invoke(DownstreamMcpInvokeRequest(
        server_id="hello.greetings", tool_name="list",
        arguments={"company_id": "co_1", "limit": 1}, claims=runtime.claims,
    ))
    assert result.result == [{"id": "downstream"}]
    assert runtime.proxy.calls[-1]["path"] == "/api/greetings/list"


async def test_real_service_enforces_durable_grant_revocation_quota_and_audit() -> None:
    runtime = await _runtime()
    registry = AdapterRegistry()
    registry.register("mcp", runtime.adapter)
    quota = CountingQuota()
    audit = InMemoryAudit()
    revocation = InMemoryRevocation()
    service = PluginCapabilityService(
        registry=registry,
        grant_store=runtime.grants,
        quota=quota,
        audit=audit,
        revocation=revocation,
    )
    result = await service.invoke(
        claims=runtime.claims,
        capability="mcp",
        operation="mcp.invoke",
        request=_invoke(runtime),
    )
    assert result.allowed
    assert result.data is not None
    assert quota.calls, "quota hook must execute before broker/backend invocation"
    assert audit.events, "successful invocation must append authoritative audit"
    assert len(runtime.proxy.calls) == 1

    foreign = replace(runtime.claims, grant_id="grant_foreign")
    missing = await service.invoke(
        claims=foreign,
        capability="mcp",
        operation="mcp.invoke",
        request=_invoke(runtime, claims=foreign),
    )
    assert not missing.allowed
    assert missing.code is DecisionCode.GRANT_MISSING
    assert len(runtime.proxy.calls) == 1

    await revocation.revoke("grant_1")
    backend_calls = len(runtime.proxy.calls)
    denied = await service.invoke(
        claims=runtime.claims,
        capability="mcp",
        operation="mcp.invoke",
        request=_invoke(runtime),
    )
    assert not denied.allowed
    assert denied.code is DecisionCode.GRANT_REVOKED
    assert len(runtime.proxy.calls) == backend_calls
