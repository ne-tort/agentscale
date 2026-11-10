"""Real MCP JSON-RPC and standalone Celery scoped job execution."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from .http_support import (
    allowed,
    assert_identity_echo,
    jsonrpc,
    mcp_arguments,
    mcp_registration_body,
    request,
    submit_job,
    wait_job,
)
from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def test_mcp_register_initialize_list_and_call_through_real_jsonrpc(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    registration = mcp_registration_body(
        world.artifact.record.manifest, world.mcp_server_id
    )
    registered = allowed(await request(
        http_client,
        "POST",
        world.api_url + "/capabilities/v1/mcp/register",
        token=world.state.token,
        payload=registration,
    ))
    assert registered["registered"] == len(registration["tools"])

    initialized = await jsonrpc(
        http_client,
        base_url=world.gateway_url,
        server_id=world.mcp_server_id,
        token=world.state.token,
        method="initialize",
        request_id="init-" + uuid.uuid4().hex,
        params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "entrypoint-acceptance", "version": "1.0.0"},
        },
    )
    assert initialized["protocolVersion"] == "2025-03-26"
    assert initialized["capabilities"] == {"tools": {"listChanged": False}}
    assert initialized["serverInfo"]["name"]
    assert await jsonrpc(
        http_client,
        base_url=world.gateway_url,
        server_id=world.mcp_server_id,
        token=world.state.token,
        method="notifications/initialized",
        request_id=None,
    ) is None
    listed = await jsonrpc(
        http_client,
        base_url=world.gateway_url,
        server_id=world.mcp_server_id,
        token=world.state.token,
        method="tools/list",
        request_id="list-" + uuid.uuid4().hex,
    )
    expected_tools = {
        item["name"] for item in world.artifact.record.manifest["mcp_servers"][0]["tools"]
    }
    assert {item["name"] for item in listed["tools"]} == expected_tools
    called = await jsonrpc(
        http_client,
        base_url=world.gateway_url,
        server_id=world.mcp_server_id,
        token=world.state.token,
        method="tools/call",
        request_id="call-" + uuid.uuid4().hex,
        params={"name": world.mcp_tool_name, "arguments": mcp_arguments(world)},
    )
    assert called["content"] and called["content"][0]["type"] == "text"
    backend_result = json.loads(called["content"][0]["text"])
    assert_identity_echo(backend_result, world=world)


async def test_standalone_worker_executes_scoped_job(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    job = await submit_job(http_client, base_url=world.api_url, world=world)
    completed = await wait_job(
        http_client,
        base_url=world.gateway_url,
        world=world,
        job=job,
        timeout=world.config.operation_timeout_seconds,
    )
    assert completed["job_id"] == job["job_id"]
    assert completed["result_digest"].startswith("sha256:")
