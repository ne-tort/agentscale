"""Single signed MCP server/tool to canonical backend RPC resolver."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from prodavan.application.plugins.gateway.ports import GatewayOperation


class SignedRpcOperationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def resolve_signed_rpc_operation(
    manifest: Mapping[str, Any], *, server_id: str, tool_name: str
) -> GatewayOperation:
    """Resolve exactly one declaration; never infer a path or another capability."""
    raw_operations = manifest.get("rpc_operations", [])
    if not isinstance(raw_operations, (list, tuple)):
        raise SignedRpcOperationError("mcp_rpc_registry_invalid")
    matches: list[GatewayOperation] = []
    for raw in raw_operations:
        if not isinstance(raw, Mapping):
            raise SignedRpcOperationError("mcp_rpc_registry_invalid")
        capability = raw.get("capability")
        if not isinstance(capability, Mapping):
            raise SignedRpcOperationError("mcp_rpc_registry_invalid")
        if capability.get("id") != server_id or raw.get("operation") != tool_name:
            continue
        try:
            matches.append(GatewayOperation(
                capability_id=capability["id"],
                capability_version=capability["version"],
                operation=raw["operation"],
                http_method=raw["http_method"],
                backend_path=raw["backend_path"],
                public=raw.get("public", False),
            ))
        except Exception:
            raise SignedRpcOperationError("mcp_rpc_registry_invalid") from None
    if len(matches) != 1:
        raise SignedRpcOperationError("mcp_rpc_operation_unknown")
    return matches[0]


__all__ = ["SignedRpcOperationError", "resolve_signed_rpc_operation"]
