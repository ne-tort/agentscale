"""Signed MCP manifest declarations; тесты НЕ запускаются во время freeze."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from prodavan.domain.plugins.types import (
    MCP_MAX_DESCRIPTION_LENGTH,
    MCP_MAX_SCHEMA_PROPERTIES,
    McpServerDeclaration,
    McpToolDeclaration,
)
from prodavan.domain.plugins.validation import (
    ManifestValidationErrorCode,
    PluginValidationError,
    load_schema,
    validate_manifest,
)

try:
    from ._fixtures import repo_root, valid_manifest_dict
except ImportError:  # запуск теста как отдельного модуля вне пакета
    from _fixtures import repo_root, valid_manifest_dict  # type: ignore[no-redef]


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"key": {"type": "string", "maxLength": 128}},
        "required": ["key"],
        "additionalProperties": False,
    }


def _tool(name: str = "list", *, description: str = "List signed values") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "input_schema": _schema(),
    }


def _server(server_id: str = "hello.greetings") -> dict[str, Any]:
    return {"server_id": server_id, "tools": [_tool("list"), _tool("greet")]}


def _payload(*servers: dict[str, Any]) -> dict[str, Any]:
    payload = valid_manifest_dict()
    payload["mcp_servers"] = list(servers or [_server()])
    return payload


def _jsonschema_validator() -> Any:
    jsonschema = pytest.importorskip("jsonschema")
    schema = load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _expect_python_rejection(payload: dict[str, Any]) -> None:
    with pytest.raises(PluginValidationError) as caught:
        validate_manifest(payload)
    assert caught.value.code is ManifestValidationErrorCode.MCP_SERVERS_INVALID


def _expect_both_rejection(payload: dict[str, Any]) -> None:
    _expect_python_rejection(payload)
    assert list(_jsonschema_validator().iter_errors(payload))


def _expect_both_acceptance(payload: dict[str, Any]) -> None:
    manifest = validate_manifest(payload)
    assert manifest.mcp_servers
    assert list(_jsonschema_validator().iter_errors(payload)) == []


def test_missing_optional_mcp_servers_is_empty_and_does_not_change_capabilities() -> None:
    payload = valid_manifest_dict()
    assert "mcp_servers" not in payload
    manifest = validate_manifest(payload)
    assert manifest.mcp_servers == ()
    assert manifest.requested_capabilities == frozenset(payload["capabilities"])


def test_valid_declarations_build_typed_dataclasses() -> None:
    manifest = validate_manifest(_payload())
    assert len(manifest.mcp_servers) == 1
    server = manifest.mcp_servers[0]
    assert isinstance(server, McpServerDeclaration)
    assert server.server_id == "hello.greetings"
    assert [tool.name for tool in server.tools] == ["list", "greet"]
    assert all(isinstance(tool, McpToolDeclaration) for tool in server.tools)
    assert all(tool.version == "1.0.0" for tool in server.tools)
    assert all(tool.input_schema["type"] == "object" for tool in server.tools)


def test_hello_manifest_declares_existing_list_and_greet_operations() -> None:
    path = repo_root() / "packages/plugins/hello-plugin/manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect_both_acceptance(payload)

    manifest = validate_manifest(payload)
    assert len(manifest.mcp_servers) == 1
    server = manifest.mcp_servers[0]
    assert server.server_id == "hello.greetings"
    tools = {tool.name: tool for tool in server.tools}
    assert set(tools) == {"list", "greet"}
    operations = {item.operation for item in manifest.rpc_operations.operations}
    assert set(tools) == operations
    assert tools["list"].input_schema["required"] == ["company_id"]
    assert tools["greet"].input_schema["required"] == ["company_id", "message"]
    assert tools["list"].input_schema["properties"]["limit"]["maximum"] == 200
    assert tools["greet"].input_schema["properties"]["message"]["maxLength"] == 4000


@pytest.mark.parametrize("server_id", ["", "Hello", "1server", "x" * 65, "https://host"])
def test_invalid_or_oversized_server_id_rejected(server_id: str) -> None:
    _expect_both_rejection(_payload(_server(server_id)))


@pytest.mark.parametrize("name", ["", "List", "1list", "x" * 65, "list-values"])
def test_invalid_or_oversized_tool_name_rejected(name: str) -> None:
    server = _server()
    server["tools"][0]["name"] = name
    _expect_both_rejection(_payload(server))


def test_duplicate_server_id_rejected_even_when_declarations_differ() -> None:
    first = _server()
    second = _server()
    second["tools"] = [_tool("other", description="Another signed declaration")]
    _expect_python_rejection(_payload(first, second))


def test_fully_identical_duplicate_server_rejected_by_python_and_schema() -> None:
    _expect_both_rejection(_payload(_server(), _server()))


def test_duplicate_tool_name_rejected_even_when_declarations_differ() -> None:
    server = _server()
    server["tools"][1]["description"] = "A different declaration with the same name"
    server["tools"][1]["name"] = "list"
    _expect_python_rejection(_payload(server))


def test_fully_identical_duplicate_tool_rejected_by_python_and_schema() -> None:
    server = _server()
    server["tools"] = [_tool("list"), _tool("list")]
    _expect_both_rejection(_payload(server))


def test_oversized_description_rejected_by_python_and_schema() -> None:
    server = _server()
    server["tools"][0]["description"] = "x" * (MCP_MAX_DESCRIPTION_LENGTH + 1)
    _expect_both_rejection(_payload(server))


def test_oversized_schema_pattern_rejected_by_python_and_schema() -> None:
    server = _server()
    server["tools"][0]["input_schema"]["pattern"] = "x" * 257
    _expect_both_rejection(_payload(server))


def test_canonical_schema_byte_cap_rejected_by_python() -> None:
    server = _server()
    # Каждый scalar отдельно валиден; суммарный canonical JSON превышает 16 KiB.
    server["tools"][0]["input_schema"]["enum"] = [
        f"{index:02d}" + "x" * 1022 for index in range(17)
    ]
    _expect_python_rejection(_payload(server))


def test_oversized_schema_property_count_rejected_by_python_and_schema() -> None:
    server = _server()
    server["tools"][0]["input_schema"]["properties"] = {
        f"p{index}": {"type": "string"}
        for index in range(MCP_MAX_SCHEMA_PROPERTIES + 1)
    }
    _expect_both_rejection(_payload(server))


def test_excessive_schema_depth_rejected_by_python_and_schema() -> None:
    schema: dict[str, Any] = {"type": "string"}
    for _ in range(8):
        schema = {
            "type": "object",
            "properties": {"child": schema},
            "additionalProperties": False,
        }
    server = _server()
    server["tools"][0]["input_schema"] = schema
    _expect_both_rejection(_payload(server))


@pytest.mark.parametrize("location,field", [
    ("server", "command"),
    ("tool", "handler"),
    ("tool", "script"),
    ("tool", "secret"),
    ("tool", "url"),
])
def test_executable_url_and_secret_fields_rejected(location: str, field: str) -> None:
    server = _server()
    target = server if location == "server" else server["tools"][0]
    target[field] = "not-allowed"
    _expect_both_rejection(_payload(server))


@pytest.mark.parametrize("field", ["password", "token", "private_key", "endpoint"])
def test_secret_or_endpoint_input_properties_rejected(field: str) -> None:
    server = _server()
    server["tools"][0]["input_schema"]["properties"][field] = {"type": "string"}
    _expect_both_rejection(_payload(server))


def test_executable_or_external_schema_refs_rejected() -> None:
    server = _server()
    server["tools"][0]["input_schema"]["$ref"] = "https://example.invalid/tool.json"
    _expect_both_rejection(_payload(server))


def test_url_in_tool_description_rejected() -> None:
    server = _server()
    server["tools"][0]["description"] = "Fetch https://example.invalid/input"
    _expect_both_rejection(_payload(server))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-01", "latest"])
def test_invalid_tool_semver_rejected_by_python_and_schema(version: str) -> None:
    server = _server()
    server["tools"][0]["version"] = version
    _expect_both_rejection(_payload(server))


def test_mcp_declaration_does_not_require_or_synthesize_mcp_capability() -> None:
    payload = _payload()
    payload["capabilities"] = ["cache"]
    manifest = validate_manifest(payload)
    assert manifest.requested_capabilities == {"cache"}
    assert len(manifest.mcp_servers) == 1


def test_input_schema_is_preserved_without_mutating_signed_data() -> None:
    payload = _payload()
    original = copy.deepcopy(payload["mcp_servers"])
    manifest = validate_manifest(payload)
    assert payload["mcp_servers"] == original
    assert set(manifest.mcp_servers[0].tools[0].input_schema) == {
        "type", "properties", "required", "additionalProperties",
    }
