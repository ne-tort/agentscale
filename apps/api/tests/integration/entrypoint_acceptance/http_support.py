"""Real HTTP/JSON-RPC helpers with secret-safe assertions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

import httpx

from prodavan.application.plugins.capabilities.adapters._shared import canonical_digest

EMPTY_DIGEST = "sha256:" + "0" * 64


def bearer(token: str, *, spoof: Mapping[str, str] | None = None) -> dict[str, str]:
    result = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if spoof:
        result.update(spoof)
    return result


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    token: str,
    payload: Any = None,
    spoof: Mapping[str, str] | None = None,
    expected: int = 200,
) -> Any:
    response = await client.request(
        method,
        url,
        headers=bearer(token, spoof=spoof),
        json=payload,
    )
    assert response.status_code == expected, (
        f"{method} {url}: expected {expected}, got {response.status_code}: "
        f"{response.text[:800]}"
    )
    if response.status_code == 202 and not response.content:
        return None
    return response.json()


def allowed(payload: Any) -> dict[str, Any]:
    assert isinstance(payload, dict) and payload.get("decision") == "allow", payload
    data = payload.get("data")
    assert isinstance(data, dict)
    return data


def mcp_registration_body(manifest: Mapping[str, Any], server_id: str) -> dict[str, Any]:
    servers = manifest.get("mcp_servers")
    assert isinstance(servers, list)
    server = next(item for item in servers if item.get("server_id") == server_id)
    tools = []
    for tool in server["tools"]:
        tools.append({
            "tool_name": tool["name"],
            "tool_version": tool["version"],
            "capability": "mcp",
            "operation": "mcp.invoke",
            "input_schema_digest": canonical_digest(tool["input_schema"]),
        })
    return {"server_id": server_id, "tools": tools}


async def jsonrpc(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    server_id: str,
    token: str,
    method: str,
    request_id: Any,
    params: Mapping[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = dict(params)
    response = await client.post(
        f"{base_url}/capabilities/v1/mcp/servers/{server_id}",
        headers=bearer(token),
        json=body,
    )
    assert response.status_code in {200, 202}, response.text[:800]
    if response.status_code == 202:
        return None
    payload = response.json()
    assert payload.get("jsonrpc") == "2.0"
    assert "error" not in payload, payload
    return payload.get("result")


def _argument(schema: Mapping[str, Any], *, world: Any, name: str = "") -> Any:
    known = {
        "company_id": world.config.company_id,
        "plugin_id": world.config.plugin_id,
        "plugin_instance_id": world.config.plugin_instance_id,
        "project_id": world.config.project_id,
        "job_id": world.config.provider_job_id,
        "target_ref": world.artifact.record.publication.backend_digest,
        "backend_digest": world.artifact.record.publication.backend_digest,
        "artifact_digest": world.artifact.record.digest,
        "arguments_digest": EMPTY_DIGEST,
        "run_id": "mcp_" + uuid.uuid4().hex,
        "request_id": "mcp_" + uuid.uuid4().hex,
        "message": "real-entrypoint-acceptance",
    }
    if name in known:
        return known[name]
    if "const" in schema:
        return schema["const"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and len(schema["enum"]) == 1:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        return {
            key: _argument(properties[key], world=world, name=key)
            for key in schema.get("required", ())
        }
    if kind == "array":
        return []
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return True
    if kind == "string":
        if schema.get("format") == "digest":
            return EMPTY_DIGEST
        return "acceptance"
    return None


def mcp_arguments(world: Any) -> dict[str, Any]:
    assert world.artifact is not None
    server = world.artifact.record.manifest["mcp_servers"][0]
    tool = next(item for item in server["tools"] if item["name"] == world.mcp_tool_name)
    schema = tool["input_schema"]
    value = _argument(schema, world=world)
    assert isinstance(value, dict)
    return value


def assert_identity_echo(value: Any, *, world: Any) -> None:
    """Find real backend-replayed headers and compare without printing tokens."""
    expected = {
        "x-plugin-id": world.config.plugin_id,
        "x-plugin-instance-id": world.config.plugin_instance_id,
        "x-plugin-artifact-digest": world.artifact.record.digest,
        "x-plugin-grant-id": world.config.grant_id,
        "x-company-id": world.config.company_id,
    }
    tracked = set(expected) | {"x-plugin-capability-token", "authorization", "x-project-id"}
    found: dict[str, Any] = {}

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(key, str) and key.lower() in tracked:
                    found[key.lower()] = child
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    for name, expected_value in expected.items():
        if name not in found:
            raise AssertionError(f"backend identity header missing: {name}")
        if found[name] != expected_value:
            raise AssertionError(f"backend identity header mismatch: {name}")
    if "x-project-id" in found and found["x-project-id"] != world.config.project_id:
        raise AssertionError("backend identity header mismatch: x-project-id")
    lowered = {str(key).lower(): child for key, child in found.items()}
    capability = lowered.get("x-plugin-capability-token")
    authorization = lowered.get("authorization")
    if capability is None or authorization is None:
        raise AssertionError("backend did not replay capability/bearer evidence")
    # MCP redaction is expected for token-shaped fields. If a backend projection
    # does not redact, require the exact relation without exposing either value.
    if capability != "[REDACTED]" and authorization != "[REDACTED]":
        assert authorization == "Bearer " + str(capability)


async def submit_job(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    world: Any,
    operation: str | None = None,
) -> dict[str, Any]:
    run_id = "run_" + uuid.uuid4().hex
    data = allowed(await request(
        client,
        "POST",
        f"{base_url}/capabilities/v1/jobs/submit",
        token=world.state.token,
        payload={
            "task_name": operation or world.job_operation,
            "run_id": run_id,
            "arguments_digest": EMPTY_DIGEST,
        },
    ))
    assert data["run_id"] == run_id and data["job_id"].startswith("job_")
    return data


async def wait_job(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    world: Any,
    job: Mapping[str, Any],
    timeout: float,
) -> dict[str, Any]:
    async def poll() -> dict[str, Any]:
        while True:
            data = allowed(await request(
                client,
                "GET",
                f"{base_url}/capabilities/v1/jobs/{job['job_id']}",
                token=world.state.token,
                payload={"run_id": job["run_id"]},
            ))
            if data["state"] == "completed":
                assert data["result_digest"] != EMPTY_DIGEST
                return data
            assert data["state"] in {"pending", "running"}, data
            await asyncio.sleep(0.1)

    return await asyncio.wait_for(poll(), timeout=timeout)


def digest_payload(value: Any) -> tuple[bytes, str]:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return raw, "sha256:" + hashlib.sha256(raw).hexdigest()
