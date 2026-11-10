"""Strict MCP JSON-RPC 2.0 adaptation over :mod:`discovery`.

This module owns protocol validation and MCP wire shapes only.  Tenant binding,
grant checks, durable discovery, typed capability invocation, quota and audit
remain in ``DurableMcpDiscovery`` and ``PluginCapabilityService``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from prodavan.application.plugins.mcp.discovery import (
    McpDiscoverRequest,
    McpDiscoveryError,
    McpInvokeRequest,
)

JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
MAX_REQUEST_BYTES = 128 * 1024
MAX_PARAMS_BYTES = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 1024

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_MESSAGES = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid Request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
}
_METHODS = frozenset({
    "initialize", "notifications/initialized", "tools/list", "tools/call",
})
_REQUEST_FIELDS = frozenset({"jsonrpc", "id", "method", "params"})
_INITIALIZE_FIELDS = frozenset({"protocolVersion", "capabilities", "clientInfo"})
_CLIENT_INFO_FIELDS = frozenset({"name", "version", "title"})
_INVALID_PARAMS_CODES = frozenset({
    "mcp_server_invalid",
    "mcp_tool_unknown",
    "mcp_arguments_invalid",
    "mcp_arguments_cap_exceeded",
})


def _result(request_id: str | int | None, value: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": value}


def _error(code: int, request_id: str | int | None = None) -> dict[str, Any]:
    # Never attach exception text, internal discovery codes, data or tracebacks.
    return {
        "jsonrpc": JSONRPC_VERSION,
        "error": {"code": code, "message": _MESSAGES[code]},
        "id": request_id,
    }


def _valid_id(value: Any) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _nonempty_text(value: Any, *, max_bytes: int = 256) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value.encode("utf-8")) <= max_bytes
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


class McpJsonRpcHandler:
    """One-request/one-response MCP handler with hard input bounds."""

    def __init__(
        self,
        *,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        server_name: str = "agentscale-api",
        server_version: str = "0.0.0",
        max_request_bytes: int = MAX_REQUEST_BYTES,
        max_params_bytes: int = MAX_PARAMS_BYTES,
        max_depth: int = MAX_DEPTH,
        max_nodes: int = MAX_NODES,
    ) -> None:
        if not _nonempty_text(protocol_version, max_bytes=64):
            raise ValueError("invalid MCP protocol version")
        if not _nonempty_text(server_name) or not _nonempty_text(server_version):
            raise ValueError("invalid MCP server info")
        if not 1 <= max_request_bytes <= 1024 * 1024:
            raise ValueError("invalid JSON-RPC request byte limit")
        if not 1 <= max_params_bytes <= max_request_bytes:
            raise ValueError("invalid JSON-RPC params byte limit")
        if not 1 <= max_depth <= 64 or not 1 <= max_nodes <= 16 * 1024:
            raise ValueError("invalid JSON-RPC structure limits")
        self.protocol_version = protocol_version
        self.server_name = server_name
        self.server_version = server_version
        self.max_request_bytes = int(max_request_bytes)
        self.max_params_bytes = int(max_params_bytes)
        self.max_depth = int(max_depth)
        self.max_nodes = int(max_nodes)

    def oversized_request_response(self) -> dict[str, Any]:
        return _error(INVALID_REQUEST)

    def internal_error_response(self) -> dict[str, Any]:
        return _error(INTERNAL_ERROR)

    def _validate_tree(self, value: Any) -> bool:
        nodes = 0

        def walk(item: Any, depth: int) -> bool:
            nonlocal nodes
            nodes += 1
            if nodes > self.max_nodes or depth > self.max_depth:
                return False
            if item is None or isinstance(item, (str, bool, int)):
                return True
            if isinstance(item, float):
                return math.isfinite(item)
            if isinstance(item, dict):
                return all(
                    isinstance(key, str) and walk(child, depth + 1)
                    for key, child in item.items()
                )
            if isinstance(item, list):
                return all(walk(child, depth + 1) for child in item)
            return False

        return walk(value, 0)

    @staticmethod
    def _ready(discovery: Any) -> bool:
        return callable(getattr(discovery, "discover", None)) and callable(
            getattr(discovery, "invoke", None)
        )

    async def handle(
        self,
        body: bytes,
        *,
        discovery: Any,
        server_id: str,
        claims: Any,
    ) -> dict[str, Any] | None:
        """Handle exactly one JSON-RPC object; ``None`` means notification."""
        if not isinstance(body, bytes) or len(body) > self.max_request_bytes:
            return _error(INVALID_REQUEST)
        try:
            request = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_json_constant,
            )
        except RecursionError:
            return _error(INVALID_REQUEST)
        except (ValueError, UnicodeDecodeError):
            return _error(PARSE_ERROR)

        if not isinstance(request, dict) or not self._validate_tree(request):
            return _error(INVALID_REQUEST)
        raw_id = request.get("id")
        request_id = raw_id if _valid_id(raw_id) else None
        has_id = "id" in request
        method = request.get("method")
        if (
            set(request) - _REQUEST_FIELDS
            or not {"jsonrpc", "method"} <= set(request)
            or request.get("jsonrpc") != JSONRPC_VERSION
            or not _valid_id(raw_id)
        ):
            return _error(INVALID_REQUEST, request_id)
        if not isinstance(method, str):
            return _error(INVALID_REQUEST, request_id)
        if method not in _METHODS:
            return _error(METHOD_NOT_FOUND, request_id)
        if method == "notifications/initialized":
            if has_id:
                return _error(INVALID_REQUEST, request_id)
        elif not has_id:
            # MCP requests are not JSON-RPC notifications; only initialized is.
            return _error(INVALID_REQUEST)

        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error(INVALID_PARAMS, request_id)
        try:
            if len(_json_bytes(params)) > self.max_params_bytes:
                return _error(INVALID_PARAMS, request_id)
        except (TypeError, ValueError, RecursionError):
            return _error(INVALID_PARAMS, request_id)

        try:
            return await self._dispatch(
                method=method,
                params=params,
                request_id=request_id,
                discovery=discovery,
                server_id=server_id,
                claims=claims,
            )
        except McpDiscoveryError as exc:
            code = INVALID_PARAMS if exc.code in _INVALID_PARAMS_CODES else INTERNAL_ERROR
            return _error(code, request_id)
        except Exception:
            # Backend and serialization errors share one sanitized response.
            return _error(INTERNAL_ERROR, request_id)

    async def _dispatch(
        self,
        *,
        method: str,
        params: dict[str, Any],
        request_id: str | int | None,
        discovery: Any,
        server_id: str,
        claims: Any,
    ) -> dict[str, Any] | None:
        if method == "notifications/initialized":
            if params:
                raise McpDiscoveryError("mcp_arguments_invalid")
            return None

        # Reuse discovery's server-id validation even during initialize.  No
        # session or tool cache is created by this stateless handshake.
        discover_request = McpDiscoverRequest(server_id=server_id, claims=claims)
        if not self._ready(discovery):
            raise McpDiscoveryError("mcp_runtime_required")

        if method == "initialize":
            self._validate_initialize(params)
            return _result(request_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.server_name, "version": self.server_version},
            })

        if method == "tools/list":
            if params:
                # Cursor-based pagination is not advertised or fabricated.
                raise McpDiscoveryError("mcp_arguments_invalid")
            discovered = await discovery.discover(discover_request)
            tools = getattr(discovered, "tools", None)
            if not isinstance(tools, (list, tuple)):
                raise McpDiscoveryError("mcp_gateway_response_invalid")
            wire: list[dict[str, Any]] = []
            for tool in tools:
                name = getattr(tool, "name", None)
                description = getattr(tool, "description", None)
                schema = getattr(tool, "input_schema", None)
                if (
                    not _nonempty_text(name)
                    or not isinstance(description, str)
                    or not isinstance(schema, Mapping)
                ):
                    raise McpDiscoveryError("mcp_gateway_response_invalid")
                wire.append({
                    "name": name,
                    "description": description,
                    "inputSchema": dict(schema),
                })
            # Canonicalization also rejects any non-JSON schema from a bad port.
            _json_bytes(wire)
            return _result(request_id, {"tools": wire})

        if method == "tools/call":
            if set(params) - {"name", "arguments"} or "name" not in params:
                raise McpDiscoveryError("mcp_arguments_invalid")
            name = params["name"]
            arguments = params.get("arguments", {})
            if not _nonempty_text(name) or not isinstance(arguments, dict):
                raise McpDiscoveryError("mcp_arguments_invalid")
            invoked = await discovery.invoke(McpInvokeRequest(
                server_id=server_id,
                tool_name=name,
                arguments=arguments,
                claims=claims,
            ))
            value = getattr(invoked, "result", None)
            if not isinstance(value, Mapping):
                raise McpDiscoveryError("mcp_gateway_response_invalid")
            # MCP 2025-03-26 returns tool data as text content.  The JSON text is
            # already the discovery layer's redacted, bounded typed projection.
            text = _json_bytes(dict(value)).decode("utf-8")
            return _result(request_id, {
                "content": [{"type": "text", "text": text}],
            })

        # The allowlist in handle() makes this unreachable; stay fail-closed.
        return _error(METHOD_NOT_FOUND, request_id)

    @staticmethod
    def _validate_initialize(params: dict[str, Any]) -> None:
        if set(params) != _INITIALIZE_FIELDS:
            raise McpDiscoveryError("mcp_arguments_invalid")
        capabilities = params["capabilities"]
        client_info = params["clientInfo"]
        if (
            not _nonempty_text(params["protocolVersion"], max_bytes=64)
            or not isinstance(capabilities, dict)
            or not isinstance(client_info, dict)
            or not {"name", "version"} <= set(client_info)
            or set(client_info) - _CLIENT_INFO_FIELDS
            or not _nonempty_text(client_info["name"])
            or not _nonempty_text(client_info["version"])
            or (
                "title" in client_info
                and not _nonempty_text(client_info["title"])
            )
        ):
            raise McpDiscoveryError("mcp_arguments_invalid")


__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSONRPC_VERSION",
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_PARAMS_BYTES",
    "MAX_REQUEST_BYTES",
    "METHOD_NOT_FOUND",
    "McpJsonRpcHandler",
    "PARSE_ERROR",
]
