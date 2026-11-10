"""Authenticated MCP JSON-RPC HTTP tests; not run during verification freeze."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.api.v1.plugin_capabilities import router as plugin_capabilities_router
from prodavan.api.v1.plugin_mcp import router as plugin_mcp_router
from prodavan.application.plugins.mcp.discovery import McpDiscoveryError
from prodavan.application.plugins.mcp.jsonrpc import MAX_REQUEST_BYTES
from prodavan.application.plugins.transport.guard import register_plugin_surface_guard
from tests.unit.plugins.transport.conftest import TEST_COMPANY, auth

PATH = "/capabilities/v1/mcp/servers/srv"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": "init-1",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "http-client", "version": "1.0.0"},
    },
}
SCHEMA = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
}


class FakeDiscovery:
    def __init__(self) -> None:
        self.discover_requests: list[Any] = []
        self.invoke_requests: list[Any] = []
        self.discover_error: Exception | None = None
        self.invoke_error: Exception | None = None

    async def discover(self, request: Any) -> Any:
        self.discover_requests.append(request)
        if self.discover_error is not None:
            raise self.discover_error
        return SimpleNamespace(tools=(SimpleNamespace(
            name="read_cache",
            description="Signed cache reader",
            input_schema=SCHEMA,
        ),))

    async def invoke(self, request: Any) -> Any:
        self.invoke_requests.append(request)
        if self.invoke_error is not None:
            raise self.invoke_error
        return SimpleNamespace(result={"found": True, "key": request.arguments["key"]})


@pytest.fixture
def mcp_discovery() -> FakeDiscovery:
    return FakeDiscovery()


@pytest.fixture
def mcp_app(keyring: Any, revocation: Any, mcp_discovery: FakeDiscovery) -> FastAPI:
    application = FastAPI()
    application.state.plugin_keyring = keyring
    application.state.plugin_revocation = revocation
    application.state.plugin_mcp_discovery = mcp_discovery
    register_plugin_surface_guard(application)
    # Production order: the specific MCP route precedes the capability catch-all.
    application.include_router(plugin_mcp_router)
    application.include_router(plugin_capabilities_router)
    return application


@pytest.fixture
def mcp_client(mcp_app: FastAPI) -> TestClient:
    return TestClient(mcp_app, raise_server_exceptions=False)


def test_initialize_requires_existing_plugin_auth(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    token: str,
) -> None:
    anonymous = mcp_client.post(PATH, json=INITIALIZE)
    assert anonymous.status_code == 401
    assert anonymous.headers["content-type"].startswith("application/problem+json")
    assert anonymous.json()["code"] == "UNAUTHORIZED"
    assert mcp_discovery.discover_requests == []

    response = mcp_client.post(PATH, json=INITIALIZE, headers=auth(token))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"]
    assert result["serverInfo"]["version"]


def test_initialized_notification_has_http_202_and_no_response_body(
    mcp_client: TestClient, token: str,
) -> None:
    response = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }, headers=auth(token))
    assert response.status_code == 202
    assert response.content == b""


def test_tools_list_and_call_use_app_state_discovery_and_verified_claims(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    token: str,
) -> None:
    listed = mcp_client.post(PATH, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }, headers=auth(token))
    assert listed.status_code == 200
    assert listed.json()["result"]["tools"] == [{
        "name": "read_cache",
        "description": "Signed cache reader",
        "inputSchema": SCHEMA,
    }]
    assert mcp_discovery.discover_requests[0].server_id == "srv"
    assert mcp_discovery.discover_requests[0].claims.company_id == TEST_COMPANY

    called = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "read_cache", "arguments": {"key": "alpha"}},
    }, headers={**auth(token), "X-Company-ID": "attacker-company"})
    assert called.status_code == 200
    text = called.json()["result"]["content"][0]["text"]
    assert json.loads(text) == {"found": True, "key": "alpha"}
    invoke = mcp_discovery.invoke_requests[0]
    assert invoke.server_id == "srv"
    assert invoke.tool_name == "read_cache"
    assert invoke.arguments == {"key": "alpha"}
    assert invoke.claims.company_id == TEST_COMPANY


def test_unknown_tool_and_invalid_arguments_use_standard_jsonrpc_errors(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    token: str,
) -> None:
    mcp_discovery.invoke_error = McpDiscoveryError("mcp_tool_unknown")
    unknown = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "not_registered", "arguments": {}},
    }, headers=auth(token))
    assert unknown.status_code == 200
    assert unknown.json()["error"] == {"code": -32602, "message": "Invalid params"}

    mcp_discovery.invoke_error = None
    calls_before = len(mcp_discovery.invoke_requests)
    invalid = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "read_cache", "arguments": ["not-an-object"]},
    }, headers=auth(token))
    assert invalid.json()["error"]["code"] == -32602
    assert len(mcp_discovery.invoke_requests) == calls_before


def test_revoke_is_denied_by_existing_dependency_before_discovery(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    revocation: Any,
    token: str,
) -> None:
    asyncio.run(revocation.revoke("grant_01"))
    response = mcp_client.post(PATH, json={
        "jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {},
    }, headers=auth(token))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "GRANT_REVOKED"
    assert mcp_discovery.discover_requests == []


def test_stale_claim_binding_is_denied_by_discovery(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    token: str,
) -> None:
    mcp_discovery.invoke_error = McpDiscoveryError("mcp_binding_denied")
    response = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "read_cache", "arguments": {"key": "alpha"}},
    }, headers=auth(token))
    assert response.status_code == 200
    assert response.json()["error"] == {"code": -32603, "message": "Internal error"}
    assert "mcp_binding_denied" not in response.text


@pytest.mark.parametrize("content,code", [
    (b'{"jsonrpc":', -32700),
    (b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]', -32600),
])
def test_malformed_json_and_batch_are_rejected(
    mcp_client: TestClient,
    token: str,
    content: bytes,
    code: int,
) -> None:
    response = mcp_client.post(
        PATH,
        content=content,
        headers={**auth(token), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == code
    assert response.json()["id"] is None


def test_streamed_oversize_body_is_rejected_before_json_parsing(
    mcp_client: TestClient, token: str,
) -> None:
    response = mcp_client.post(
        PATH,
        content=b" " * (MAX_REQUEST_BYTES + 1),
        headers={**auth(token), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["error"] == {"code": -32600, "message": "Invalid Request"}


def test_no_secret_dsn_or_backend_stack_leaks_from_http_error(
    mcp_client: TestClient,
    mcp_discovery: FakeDiscovery,
    token: str,
) -> None:
    secret = "plaintext-http-secret"
    dsn = "postgresql://user:plaintext-password@private-host/db"
    mcp_discovery.invoke_error = RuntimeError(f"{secret} {dsn}")
    response = mcp_client.post(PATH, json={
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "read_cache", "arguments": {"key": "alpha"}},
    }, headers=auth(token))
    assert response.json() == {
        "jsonrpc": "2.0",
        "error": {"code": -32603, "message": "Internal error"},
        "id": 8,
    }
    assert secret not in response.text
    assert dsn not in response.text
    assert "Traceback" not in response.text


def test_missing_discovery_does_not_fake_tool_capability(
    mcp_app: FastAPI,
    token: str,
) -> None:
    del mcp_app.state.plugin_mcp_discovery
    client = TestClient(mcp_app, raise_server_exceptions=False)
    response = client.post(PATH, json=INITIALIZE, headers=auth(token))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32603
    assert "result" not in response.json()


def test_unimplemented_mcp_namespaces_are_method_not_found(
    mcp_client: TestClient, token: str,
) -> None:
    for method in ("resources/list", "prompts/list", "logging/setLevel"):
        response = mcp_client.post(PATH, json={
            "jsonrpc": "2.0", "id": 9, "method": method, "params": {},
        }, headers=auth(token))
        assert response.json()["error"] == {
            "code": -32601,
            "message": "Method not found",
        }
