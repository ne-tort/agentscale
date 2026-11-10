"""Durable MCP registration/discovery over two real Redis client adapters."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from prodavan.application.plugins.admin.contracts import GrantStatus
from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    canonical_digest,
)
from prodavan.application.plugins.capabilities.adapters.mcp import DurableMcpAdapter
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    CapabilityName,
    McpInvokeRequest,
    McpRegisterRequest,
    McpToolDefinition,
)
from prodavan.application.plugins.capabilities.quotas import PluginQuota
from prodavan.application.plugins.mcp.broker import DurableMcpBroker
from prodavan.application.plugins.mcp.discovery import (
    ArtifactGrantMcpManifestSource,
    DurableMcpDiscovery,
    McpDiscoverRequest,
    McpDiscoveryError,
)
from prodavan.application.plugins.mcp.rpc import resolve_signed_rpc_operation
from prodavan.infrastructure.plugins.capabilities.redis import RedisMcpRegistrationStore

pytestmark = [pytest.mark.asyncio, pytest.mark.redis_acceptance]

DIGEST_ONE = "sha256:" + "1" * 64
DIGEST_TWO = "sha256:" + "2" * 64
SERVER_ID = "hello.greetings"
LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "company_id": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
    "required": ["company_id"],
    "additionalProperties": False,
}
GREET_SCHEMA = {
    "type": "object",
    "properties": {
        "company_id": {"type": "string", "minLength": 1},
        "message": {"type": "string", "minLength": 1, "maxLength": 100},
    },
    "required": ["company_id", "message"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Claims:
    company_id: str = "mcp_company"
    plugin_id: str = "hello_plugin"
    plugin_instance_id: str = "mcp_instance"
    artifact_digest: str = DIGEST_ONE
    manifest_version: str = "1.0.0"
    grant_id: str = "mcp_grant"
    scopes: frozenset[str] = frozenset({"mcp"})
    exp: int = 2_000_000_000
    cabinet_id: str | None = None
    project_id: str | None = None

    @property
    def sub(self) -> str:
        return f"plugin:{self.plugin_id}:{self.company_id}:{self.plugin_instance_id}"


def envelope_for(claims: Claims, request_id: str) -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id=request_id,
        company_id=claims.company_id,
        subject_id=claims.sub,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        grant_id=claims.grant_id,
        cabinet_id=claims.cabinet_id,
        project_id=claims.project_id,
    )


def definition(name: str, schema: dict[str, Any]) -> McpToolDefinition:
    return McpToolDefinition(
        tool_name=name,
        tool_version="1.0.0",
        capability=CapabilityName.MCP,
        operation="mcp.invoke",
        input_schema_digest=canonical_digest(schema),
    )


def register_request(
    claims: Claims, request_id: str, *, tools: tuple[McpToolDefinition, ...] | None = None
) -> McpRegisterRequest:
    return McpRegisterRequest(
        server_id=SERVER_ID,
        tools=tools or (
            definition("list", LIST_SCHEMA),
            definition("greet", GREET_SCHEMA),
        ),
        envelope=envelope_for(claims, request_id),
    )


def invoke_request(
    claims: Claims,
    *,
    request_id: str = "mcp-invoke",
    tool_name: str = "list",
    arguments: dict[str, Any] | None = None,
) -> McpInvokeRequest:
    values = arguments or {"company_id": claims.company_id, "limit": 2}
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return McpInvokeRequest(
        server_id=SERVER_ID,
        tool_name=tool_name,
        arguments_json=payload,
        arguments_digest="sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        manifest_version=claims.manifest_version,
        envelope=envelope_for(claims, request_id),
    )


def manifest_for(claims: Claims) -> dict[str, Any]:
    return {
        "plugin_id": claims.plugin_id,
        "version": claims.manifest_version,
        "mcp_servers": [{
            "server_id": SERVER_ID,
            "tools": [
                {
                    "name": "list",
                    "description": "List greetings through the signed backend operation.",
                    "version": "1.0.0",
                    "input_schema": LIST_SCHEMA,
                },
                {
                    "name": "greet",
                    "description": "Create a greeting through the signed backend operation.",
                    "version": "1.0.0",
                    "input_schema": GREET_SCHEMA,
                },
            ],
        }],
        "rpc_operations": [
            {
                "capability": {"id": SERVER_ID, "version": "1.0"},
                "operation": "list",
                "http_method": "GET",
                "backend_path": "/api/greetings/list",
                "public": False,
            },
            {
                "capability": {"id": SERVER_ID, "version": "1.0"},
                "operation": "greet",
                "http_method": "POST",
                "backend_path": "/api/greetings",
                "public": False,
            },
        ],
    }


def grant_for(claims: Claims) -> Any:
    now = datetime.now(UTC)
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
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
    )


def artifact_for(claims: Claims) -> Any:
    return SimpleNamespace(
        plugin_id=claims.plugin_id,
        version=claims.manifest_version,
        digest=claims.artifact_digest,
        published=True,
        verification=object(),
        manifest=manifest_for(claims),
    )


class GrantStore:
    def __init__(self, claims: Claims) -> None:
        self.records = {claims.grant_id: grant_for(claims)}

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return self.records.get(grant_id)


class ArtifactRegistry:
    def __init__(self, claims: Claims) -> None:
        self.records = {(claims.plugin_id, claims.manifest_version): artifact_for(claims)}

    async def get_async(self, plugin_id: str, version: str) -> Any | None:
        return self.records.get((plugin_id, version))


class RecordingBackend:
    """Deterministic non-Redis backend boundary; records the signed RPC projection."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def forward(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            status_code=200,
            body=b'[{"id":"backend-result"}]',
            stream=None,
        )


class AdapterGateway:
    def __init__(self, adapter: DurableMcpAdapter) -> None:
        self.adapter = adapter

    async def invoke(
        self, *, claims: Any, capability: str, operation: str, request: Any
    ) -> Any:
        if capability != "mcp" or operation not in {"invoke", "mcp.invoke"}:
            return SimpleNamespace(allowed=False)
        return SimpleNamespace(allowed=True, data=await self.adapter.invoke(request))


class McpRuntime:
    def __init__(self, redis_pair: Any) -> None:
        self.claims = Claims()
        self.api_store = RedisMcpRegistrationStore(client=redis_pair.api)
        self.gateway_store = RedisMcpRegistrationStore(client=redis_pair.gateway)
        self.api_adapter = DurableMcpAdapter(store=self.api_store)
        self.gateway_adapter = DurableMcpAdapter(store=self.gateway_store)
        self.grants = GrantStore(self.claims)
        self.artifacts = ArtifactRegistry(self.claims)
        self.source = ArtifactGrantMcpManifestSource(
            grant_store=self.grants, artifact_registry=self.artifacts,
        )
        self.backend = RecordingBackend()
        self.discovery = DurableMcpDiscovery(
            store=self.gateway_store,
            manifest_source=self.source,
            gateway=AdapterGateway(self.gateway_adapter),
        )
        self.broker = DurableMcpBroker(
            discovery=self.discovery,
            manifest_source=self.source,
            grant_store=self.grants,
            backend_proxy=self.backend,
        )
        self.gateway_adapter.attach_broker(self.broker)

    async def aclose(self) -> None:
        await asyncio.gather(
            self.api_adapter.aclose(),
            self.gateway_adapter.aclose(),
            self.discovery.aclose(),
        )


@pytest_asyncio.fixture
async def mcp_runtime(redis_pair: Any) -> Any:
    runtime = McpRuntime(redis_pair)
    try:
        yield runtime
    finally:
        await runtime.aclose()


async def test_registration_store_maps_real_nx_miss_to_false_without_overwrite(
    redis_pair: Any,
) -> None:
    api = RedisMcpRegistrationStore(client=redis_pair.api)
    gateway = RedisMcpRegistrationStore(client=redis_pair.gateway)
    scope = {
        "company_id": "mcp_company",
        "plugin_id": "hello_plugin",
        "plugin_instance_id": "mcp_instance",
        "server_id": "nx-replay-scope",
    }

    assert await api.put_if_absent(**scope, definitions="canonical-v1") is True
    # redis.asyncio returns None for this second SET NX.  The durable store port
    # must translate that real miss to False so the adapter can re-read/replay.
    assert await gateway.put_if_absent(**scope, definitions="canonical-v1") is False
    assert await gateway.put_if_absent(**scope, definitions="conflicting-v2") is False
    assert await api.get(**scope) == "canonical-v1"


async def test_real_reread_instrumentation_requires_real_nx_miss(
    redis_pair: Any,
) -> None:
    assert await redis_pair.api.get("instrumentation-initial-missing") is None
    assert redis_pair.api.nx_miss_reread_values == []
    api = RedisMcpRegistrationStore(client=redis_pair.api)
    gateway = RedisMcpRegistrationStore(client=redis_pair.gateway)
    scope = {
        "company_id": "mcp_company",
        "plugin_id": "hello_plugin",
        "plugin_instance_id": "mcp_instance",
        "server_id": "instrumentation-nx-scope",
    }
    assert await api.put_if_absent(**scope, definitions="canonical-v1") is True
    assert await gateway.put_if_absent(**scope, definitions="canonical-v1") is False
    assert redis_pair.gateway.nx_miss_reread_values == []
    value = await gateway.get(**scope)
    assert value == "canonical-v1"
    assert len(redis_pair.gateway.nx_miss_reread_values) == 1
    assert redis_pair.gateway.nx_miss_reread_values[0][1] == value.encode("utf-8")
    assert isinstance(redis_pair.gateway.nx_miss_reread_values[0][1], bytes)
    assert await gateway.get(**scope) == value
    assert len(redis_pair.gateway.nx_miss_reread_values) == 1


async def test_two_clients_concurrent_register_replay_and_conflict_keep_one_registry(
    redis_pair: Any, mcp_runtime: McpRuntime,
) -> None:
    runtime = mcp_runtime
    api_request = register_request(runtime.claims, "api-register")
    gateway_request = register_request(runtime.claims, "gateway-register")

    # Deterministic schedule: both initial GETs observe missing, both SET NX calls
    # finish before either adapter performs its mandatory verification re-read.
    initial_get = asyncio.Barrier(2)
    set_entry = asyncio.Barrier(2)
    set_exit = asyncio.Barrier(2)
    redis_pair.api.initial_get_barriers = [initial_get]
    redis_pair.gateway.initial_get_barriers = [initial_get]
    redis_pair.api.set_entry_barrier = set_entry
    redis_pair.gateway.set_entry_barrier = set_entry
    redis_pair.api.set_exit_barrier = set_exit
    redis_pair.gateway.set_exit_barrier = set_exit

    api_response, gateway_response = await asyncio.gather(
        runtime.api_adapter.register(api_request),
        runtime.gateway_adapter.register(gateway_request),
    )
    assert api_response.registered == 2
    assert gateway_response.registered == 2
    assert api_response.digest == gateway_response.digest
    assert runtime.api_store is not runtime.gateway_store

    nx_results = [*redis_pair.api.nx_results, *redis_pair.gateway.nx_results]
    assert len(nx_results) == 2
    assert sum(result is True for result in nx_results) == 1
    assert sum(result is None for result in nx_results) == 1
    winner = (
        redis_pair.api
        if redis_pair.api.nx_results and redis_pair.api.nx_results[0] is True
        else redis_pair.gateway
    )
    loser = redis_pair.gateway if winner is redis_pair.api else redis_pair.api
    assert loser.nx_results and loser.nx_results[0] is None
    assert len(loser.nx_miss_reread_values) == 1
    # The real loser returned False to the adapter, then re-read the winner's
    # exact definitions; neither a fabricated success nor an overwrite occurred.
    keys = await redis_pair.mcp_registration_keys()
    assert len(keys) == 1
    durable_before_replay = await redis_pair.clients[0].get(keys[0])
    assert durable_before_replay is not None
    assert loser.nx_miss_reread_values[0][1] == durable_before_replay

    # Exact replay through either client is idempotent, never last-writer-wins.
    assert (await runtime.api_adapter.register(api_request)).digest == api_response.digest
    assert (await runtime.gateway_adapter.register(gateway_request)).digest == api_response.digest
    keys = await redis_pair.mcp_registration_keys()
    assert len(keys) == 1
    before = await redis_pair.clients[0].get(keys[0])
    assert before is not None

    conflict = register_request(
        runtime.claims,
        "gateway-conflict",
        tools=(definition("changed", LIST_SCHEMA),),
    )
    with pytest.raises(CapabilityAccessDenied, match="mcp_registration_conflict"):
        await runtime.gateway_adapter.register(conflict)
    assert await redis_pair.clients[0].get(keys[0]) == before
    assert len(await redis_pair.mcp_registration_keys()) == 1


async def test_two_clients_foreign_race_conflicts_after_real_nx_miss_and_reread(
    redis_pair: Any, mcp_runtime: McpRuntime,
) -> None:
    runtime = mcp_runtime
    winner_request = register_request(runtime.claims, "race-winner")
    foreign_request = register_request(
        runtime.claims,
        "race-foreign",
        tools=(definition("changed", LIST_SCHEMA),),
    )
    initial_get = asyncio.Barrier(2)
    set_entry = asyncio.Barrier(2)
    set_exit = asyncio.Barrier(2)
    redis_pair.api.initial_get_barriers = [initial_get]
    redis_pair.gateway.initial_get_barriers = [initial_get]
    redis_pair.api.set_entry_barrier = set_entry
    redis_pair.gateway.set_entry_barrier = set_entry
    redis_pair.api.set_exit_barrier = set_exit
    redis_pair.gateway.set_exit_barrier = set_exit

    first, second = await asyncio.gather(
        runtime.api_adapter.register(winner_request),
        runtime.gateway_adapter.register(foreign_request),
        return_exceptions=True,
    )
    outcomes = (first, second)
    successes = [item for item in outcomes if not isinstance(item, Exception)]
    conflicts = [item for item in outcomes if isinstance(item, CapabilityAccessDenied)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert "mcp_registration_conflict" in str(conflicts[0])

    nx_results = [*redis_pair.api.nx_results, *redis_pair.gateway.nx_results]
    assert sum(result is True for result in nx_results) == 1
    assert sum(result is None for result in nx_results) == 1
    winner = (
        redis_pair.api
        if redis_pair.api.nx_results and redis_pair.api.nx_results[0] is True
        else redis_pair.gateway
    )
    loser = redis_pair.gateway if winner is redis_pair.api else redis_pair.api
    assert len(loser.nx_miss_reread_values) == 1
    keys = await redis_pair.mcp_registration_keys()
    assert len(keys) == 1
    durable = await redis_pair.clients[0].get(keys[0])
    assert durable is not None
    # The foreign request loses NX, re-reads the winner's different definitions,
    # and only then conflicts. The foreign value never overwrites durable state.
    assert loser.nx_miss_reread_values[0][1] == durable
    if winner is redis_pair.api:
        replay_adapter = runtime.api_adapter
        other_adapter = runtime.gateway_adapter
        replay_request = winner_request
    else:
        replay_adapter = runtime.gateway_adapter
        other_adapter = runtime.api_adapter
        replay_request = foreign_request
    # Select the actual winner's definitions and adapter, regardless of which
    # independent client wins. The same winning request is idempotent via both.
    replay = await replay_adapter.register(replay_request)
    other_replay = await other_adapter.register(replay_request)
    assert replay.digest == successes[0].digest
    assert other_replay.digest == successes[0].digest
    assert len(await redis_pair.mcp_registration_keys()) == 1
    assert await redis_pair.clients[0].get(keys[0]) == durable


async def test_discovery_and_broker_use_canonical_signed_rpc_mapping(
    mcp_runtime: McpRuntime,
) -> None:
    runtime = mcp_runtime
    registered = await runtime.api_adapter.register(
        register_request(runtime.claims, "api-register-discovery")
    )
    discovered = await runtime.discovery.discover(
        McpDiscoverRequest(server_id=SERVER_ID, claims=runtime.claims)
    )
    assert [tool.name for tool in discovered.tools] == ["list", "greet"]
    assert {tool.capability for tool in discovered.tools} == {"mcp"}
    assert {tool.operation for tool in discovered.tools} == {"mcp.invoke"}
    assert {tool.artifact_digest for tool in discovered.tools} == {DIGEST_ONE}
    assert discovered.registration_digest == registered.digest

    artifact = runtime.artifacts.records[("hello_plugin", "1.0.0")]
    signed = resolve_signed_rpc_operation(
        artifact.manifest, server_id=SERVER_ID, tool_name="list",
    )
    assert signed.capability_id == SERVER_ID
    assert signed.capability_version == "1.0"
    assert signed.http_method == "GET"
    assert signed.backend_path == "/api/greetings/list"

    response = await runtime.gateway_adapter.invoke(invoke_request(runtime.claims))
    assert response.ok is True
    assert response.operation == "list"
    assert response.backend_path == "/api/greetings/list"
    assert json.loads(response.response_json) == [{"id": "backend-result"}]
    assert len(runtime.backend.calls) == 1
    call = runtime.backend.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/greetings/list"
    assert call["body"] == b""
    assert call["query_string"] == b"company_id=mcp_company&limit=2"


async def test_registered_tool_without_signed_rpc_mapping_is_denied(
    mcp_runtime: McpRuntime,
) -> None:
    runtime = mcp_runtime
    await runtime.gateway_adapter.register(
        register_request(runtime.claims, "gateway-register-unmapped")
    )
    artifact = runtime.artifacts.records[("hello_plugin", "1.0.0")]
    artifact.manifest["rpc_operations"] = []

    with pytest.raises(McpDiscoveryError, match="mcp_rpc_operation_unknown"):
        await runtime.discovery.discover(
            McpDiscoverRequest(server_id=SERVER_ID, claims=runtime.claims)
        )
    with pytest.raises(CapabilityAccessDenied, match="mcp_rpc_operation_unknown"):
        await runtime.gateway_adapter.invoke(invoke_request(runtime.claims))
    assert runtime.backend.calls == []


async def test_stale_digest_foreign_binding_and_revoked_grant_are_denied(
    redis_pair: Any, mcp_runtime: McpRuntime,
) -> None:
    runtime = mcp_runtime
    original_registration = await runtime.api_adapter.register(
        register_request(runtime.claims, "api-register-binding")
    )
    original_invoke = invoke_request(runtime.claims, request_id="original-invoke")

    foreign = replace(runtime.claims, grant_id="foreign_grant")
    with pytest.raises(CapabilityAccessDenied, match="mcp_binding_denied"):
        await runtime.gateway_adapter.invoke(
            invoke_request(foreign, request_id="foreign-invoke")
        )
    assert runtime.backend.calls == []

    grant = runtime.grants.records[runtime.claims.grant_id]
    grant.status = GrantStatus.REVOKED
    grant.is_active = False
    grant.revoked_at = datetime.now(UTC)
    with pytest.raises(CapabilityAccessDenied, match="mcp_binding_denied"):
        await runtime.gateway_adapter.invoke(original_invoke)
    assert runtime.backend.calls == []

    # Roll out the same signed declarations under a new immutable digest.  The
    # old request is now foreign, and the old registration is not reused.
    new_claims = replace(runtime.claims, artifact_digest=DIGEST_TWO)
    grant.status = GrantStatus.ACTIVE
    grant.is_active = True
    grant.revoked_at = None
    grant.artifact_digest = DIGEST_TWO
    runtime.artifacts.records[("hello_plugin", "1.0.0")] = artifact_for(new_claims)

    with pytest.raises(CapabilityAccessDenied, match="mcp_binding_denied"):
        await runtime.gateway_adapter.invoke(original_invoke)
    empty = await runtime.discovery.discover(
        McpDiscoverRequest(server_id=SERVER_ID, claims=new_claims)
    )
    assert empty.tools == ()
    with pytest.raises(CapabilityAccessDenied, match="mcp_tool_unknown"):
        await runtime.gateway_adapter.invoke(
            invoke_request(new_claims, request_id="stale-registration-invoke")
        )
    assert runtime.backend.calls == []
    assert len(await redis_pair.mcp_registration_keys()) == 1

    # A fresh digest-scoped registration creates a separate entry and is then
    # discoverable; it does not mutate the old artifact's durable definition.
    fresh = await runtime.gateway_adapter.register(
        register_request(new_claims, "gateway-register-new-digest")
    )
    assert fresh.digest != original_registration.digest
    assert len(await redis_pair.mcp_registration_keys()) == 2
    discovered = await runtime.discovery.discover(
        McpDiscoverRequest(server_id=SERVER_ID, claims=new_claims)
    )
    assert [tool.name for tool in discovered.tools] == ["list", "greet"]
    assert {tool.artifact_digest for tool in discovered.tools} == {DIGEST_TWO}
