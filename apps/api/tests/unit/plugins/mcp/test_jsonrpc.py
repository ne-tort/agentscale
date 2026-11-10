"""MCP JSON-RPC contract tests; execution is deferred by verification freeze."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.mcp.discovery import (
    McpDiscoverRequest,
    McpDiscoveryError,
    McpDiscoveryResult,
    McpInvokeRequest,
    McpInvokeResult,
    McpTool,
    McpUsage,
)
from prodavan.application.plugins.mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    McpJsonRpcHandler,
)
from prodavan.application.plugins.wiring.adapters import (
    aclose_adapter_registry,
    attach_mcp_discovery,
)

pytestmark = pytest.mark.asyncio
DIGEST = "sha256:" + "a" * 64
SCHEMA = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
    "additionalProperties": False,
}
INITIALIZE_PARAMS = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "contract-client", "version": "1.0.0"},
}
_UNSET = object()


def _tool(name: str = "read_cache") -> McpTool:
    return McpTool(
        server_id="srv",
        name=name,
        version="1.0.0",
        description="Signed cache reader",
        input_schema=SCHEMA,
        capability="mcp",
        operation="mcp.invoke",
        input_schema_digest=DIGEST,
        artifact_digest=DIGEST,
        registration_digest=DIGEST,
    )


class FakeDiscovery:
    def __init__(self) -> None:
        self.tools = (_tool(),)
        self.result: dict[str, Any] = {"found": True, "value": "safe"}
        self.discover_requests: list[McpDiscoverRequest] = []
        self.invoke_requests: list[McpInvokeRequest] = []
        self.discover_error: Exception | None = None
        self.invoke_error: Exception | None = None
        self.close_calls = 0
        self.close_invocations = 0
        self.closed = False

    async def discover(self, request: McpDiscoverRequest) -> McpDiscoveryResult:
        self.discover_requests.append(request)
        if self.discover_error is not None:
            raise self.discover_error
        return McpDiscoveryResult(
            binding=SimpleNamespace(),
            server_id=request.server_id,
            registration_digest=DIGEST,
            tools=tuple(self.tools),
            response_bytes=0,
        )

    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResult:
        self.invoke_requests.append(request)
        if self.invoke_error is not None:
            raise self.invoke_error
        return McpInvokeResult(
            binding=SimpleNamespace(),
            server_id=request.server_id,
            tool_name=request.tool_name,
            result=self.result,
            result_digest=DIGEST,
            usage=McpUsage(arguments_bytes=1, response_bytes=1),
        )

    async def aclose(self) -> None:
        self.close_invocations += 1
        if self.closed:
            return
        self.closed = True
        self.close_calls += 1


def _body(
    method: str,
    params: Any = _UNSET,
    *,
    request_id: Any = 1,
    include_id: bool = True,
) -> bytes:
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if include_id:
        request["id"] = request_id
    if params is not _UNSET:
        request["params"] = params
    return json.dumps(request, separators=(",", ":")).encode("utf-8")


async def _handle(
    handler: McpJsonRpcHandler,
    discovery: FakeDiscovery,
    body: bytes,
) -> dict[str, Any] | None:
    return await handler.handle(
        body,
        discovery=discovery,
        server_id="srv",
        claims=SimpleNamespace(company_id="co_1"),
    )


async def test_initialize_and_initialized_notification() -> None:
    handler = McpJsonRpcHandler(
        protocol_version="2025-06-18",
        server_name="agentscale-test",
        server_version="2.3.4",
    )
    discovery = FakeDiscovery()
    response = await _handle(
        handler, discovery, _body("initialize", INITIALIZE_PARAMS, request_id="init-1")
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": "init-1",
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agentscale-test", "version": "2.3.4"},
        },
    }
    assert set(response["result"]["capabilities"]) == {"tools"}
    assert discovery.discover_requests == []
    assert discovery.invoke_requests == []

    notification = await _handle(
        handler,
        discovery,
        _body("notifications/initialized", include_id=False),
    )
    assert notification is None


async def test_tools_list_projects_only_real_discovery_tools() -> None:
    handler = McpJsonRpcHandler()
    discovery = FakeDiscovery()
    response = await _handle(handler, discovery, _body("tools/list", {}))
    assert response["result"] == {
        "tools": [{
            "name": "read_cache",
            "description": "Signed cache reader",
            "inputSchema": SCHEMA,
        }],
    }
    assert set(response["result"]["tools"][0]) == {
        "name", "description", "inputSchema",
    }
    request = discovery.discover_requests[0]
    assert isinstance(request, McpDiscoverRequest)
    assert request.server_id == "srv"
    assert request.claims.company_id == "co_1"

    discovery.tools = ()
    empty = await _handle(handler, discovery, _body("tools/list", request_id=None))
    assert empty["id"] is None
    assert empty["result"] == {"tools": []}


async def test_tools_call_uses_discovery_invoke_and_mcp_content() -> None:
    handler = McpJsonRpcHandler()
    discovery = FakeDiscovery()
    response = await _handle(handler, discovery, _body(
        "tools/call",
        {"name": "read_cache", "arguments": {"key": "alpha"}},
        request_id=7,
    ))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 7
    content = response["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"]) == {"found": True, "value": "safe"}
    assert "structuredContent" not in response["result"]
    request = discovery.invoke_requests[0]
    assert isinstance(request, McpInvokeRequest)
    assert request.server_id == "srv"
    assert request.tool_name == "read_cache"
    assert request.arguments == {"key": "alpha"}
    assert request.claims.company_id == "co_1"


async def test_unknown_tool_and_typed_invalid_arguments_are_invalid_params() -> None:
    handler = McpJsonRpcHandler()
    discovery = FakeDiscovery()
    discovery.invoke_error = McpDiscoveryError("mcp_tool_unknown")
    unknown = await _handle(handler, discovery, _body(
        "tools/call", {"name": "missing", "arguments": {}},
    ))
    assert unknown["error"] == {"code": INVALID_PARAMS, "message": "Invalid params"}

    discovery.invoke_error = McpDiscoveryError("mcp_arguments_invalid")
    invalid = await _handle(handler, discovery, _body(
        "tools/call", {"name": "read_cache", "arguments": {"key": 3}},
    ))
    assert invalid["error"]["code"] == INVALID_PARAMS

    discovery.invoke_error = None
    calls_before = len(discovery.invoke_requests)
    malformed = await _handle(handler, discovery, _body(
        "tools/call", {"name": "read_cache", "arguments": []},
    ))
    assert malformed["error"]["code"] == INVALID_PARAMS
    assert len(discovery.invoke_requests) == calls_before


@pytest.mark.parametrize("method,params", [
    ("tools/list", {}),
    ("tools/call", {"name": "read_cache", "arguments": {"key": "alpha"}}),
])
async def test_revoked_binding_is_denied(method: str, params: dict[str, Any]) -> None:
    discovery = FakeDiscovery()
    discovery.discover_error = McpDiscoveryError("mcp_binding_denied")
    discovery.invoke_error = McpDiscoveryError("mcp_binding_denied")
    response = await _handle(McpJsonRpcHandler(), discovery, _body(method, params))
    assert response["error"]["code"] == INTERNAL_ERROR
    assert "mcp_binding_denied" not in json.dumps(response)


async def test_stale_artifact_binding_is_denied_without_tool_fallback() -> None:
    discovery = FakeDiscovery()
    discovery.invoke_error = McpDiscoveryError("mcp_binding_denied")
    response = await _handle(McpJsonRpcHandler(), discovery, _body(
        "tools/call", {"name": "read_cache", "arguments": {"key": "alpha"}},
    ))
    assert response["error"]["code"] == INTERNAL_ERROR
    assert "result" not in response


@pytest.mark.parametrize("body,code", [
    (b'{"jsonrpc":"2.0","method":', PARSE_ERROR),
    (b'[{"jsonrpc":"2.0","method":"tools/list","id":1}]', INVALID_REQUEST),
    (b'{"jsonrpc":"2.0","method":"tools/list","id":1,"id":2}', PARSE_ERROR),
    (b'{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"x","arguments":{"v":NaN}}}', PARSE_ERROR),
])
async def test_malformed_json_batch_and_ambiguous_objects_rejected(
    body: bytes, code: int,
) -> None:
    response = await _handle(McpJsonRpcHandler(), FakeDiscovery(), body)
    assert response["error"]["code"] == code
    assert response["id"] is None


async def test_oversize_and_structure_limits_are_enforced() -> None:
    discovery = FakeDiscovery()
    small = McpJsonRpcHandler(max_request_bytes=128, max_params_bytes=32)
    oversized = await _handle(small, discovery, b" " * 129)
    assert oversized["error"]["code"] == INVALID_REQUEST
    params_oversized = await _handle(McpJsonRpcHandler(
        max_request_bytes=1024,
        max_params_bytes=32,
    ), discovery, _body(
        "tools/call",
        {"name": "read_cache", "arguments": {"key": "x" * 64}},
    ))
    assert params_oversized["error"]["code"] == INVALID_PARAMS

    deep_arguments: dict[str, Any] = {}
    cursor = deep_arguments
    for _ in range(8):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    deep = await _handle(McpJsonRpcHandler(max_depth=4), discovery, _body(
        "tools/call", {"name": "read_cache", "arguments": deep_arguments},
    ))
    assert deep["error"]["code"] == INVALID_REQUEST

    many_nodes = await _handle(McpJsonRpcHandler(max_nodes=8), discovery, _body(
        "tools/call",
        {"name": "read_cache", "arguments": {str(index): index for index in range(20)}},
    ))
    assert many_nodes["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize("body,code", [
    (_body("resources/list", {}), METHOD_NOT_FOUND),
    (_body("prompts/list", {}), METHOD_NOT_FOUND),
    (_body("logging/setLevel", {"level": "debug"}), METHOD_NOT_FOUND),
    (_body("tools/list", {}, request_id=True), INVALID_REQUEST),
    (_body("tools/list", {}, include_id=False), INVALID_REQUEST),
    (_body("notifications/initialized", {}, request_id=None), INVALID_REQUEST),
    (_body("tools/list", {"cursor": "not-implemented"}), INVALID_PARAMS),
    (_body("initialize", {}), INVALID_PARAMS),
])
async def test_allowlist_ids_and_method_params_are_strict(body: bytes, code: int) -> None:
    response = await _handle(McpJsonRpcHandler(), FakeDiscovery(), body)
    assert response["error"]["code"] == code


async def test_backend_failures_never_leak_secret_dsn_or_internal_code() -> None:
    secret = "plaintext-token-never-cross-jsonrpc"
    dsn = "redis://user:plaintext-password@private-host/0"
    discovery = FakeDiscovery()
    discovery.invoke_error = RuntimeError(f"backend failed: {secret}; {dsn}")
    response = await _handle(McpJsonRpcHandler(), discovery, _body(
        "tools/call", {"name": "read_cache", "arguments": {"key": "alpha"}},
    ))
    encoded = json.dumps(response)
    assert response == {
        "jsonrpc": "2.0",
        "error": {"code": INTERNAL_ERROR, "message": "Internal error"},
        "id": 1,
    }
    assert secret not in encoded
    assert dsn not in encoded
    assert "RuntimeError" not in encoded
    assert "Traceback" not in encoded
    assert "mcp_" not in encoded


async def test_composed_app_state_discovery_is_closed_once() -> None:
    discovery = FakeDiscovery()
    app_state = SimpleNamespace(plugin_mcp_discovery=discovery)
    registry = AdapterRegistry()
    attach_mcp_discovery(registry, discovery)
    await aclose_adapter_registry(registry)
    await aclose_adapter_registry(registry)
    await discovery.aclose()
    # Adapter registry owns shutdown; the route adds no second lifecycle owner.
    # CloseOnce semantics make both repeated shutdown and direct close no-ops.
    assert discovery.close_invocations == 2  # one registry shutdown + direct call
    assert discovery.close_calls == 1
    assert getattr(registry, "_mcp_discovery") is discovery
    assert app_state.plugin_mcp_discovery is discovery
