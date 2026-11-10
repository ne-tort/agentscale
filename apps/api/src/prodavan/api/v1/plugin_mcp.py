"""Authenticated internal MCP JSON-RPC surface for declared plugin servers."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response

from prodavan.api.v1.plugin_capabilities import PluginRoute
from prodavan.application.plugins.mcp.jsonrpc import (
    DEFAULT_PROTOCOL_VERSION,
    McpJsonRpcHandler,
)
from prodavan.application.plugins.transport.deps import PluginClaimsDep
from prodavan.config.settings import settings


def _configured_handler() -> McpJsonRpcHandler:
    protocol_version = (
        getattr(settings, "plugin_mcp_protocol_version", None)
        or getattr(settings, "mcp_protocol_version", None)
        or DEFAULT_PROTOCOL_VERSION
    )
    return McpJsonRpcHandler(
        protocol_version=protocol_version,
        server_name=(
            getattr(settings, "plugin_mcp_server_name", None) or settings.app_name
        ),
        server_version=(
            getattr(settings, "plugin_mcp_server_version", None) or settings.app_version
        ),
    )


_handler = _configured_handler()
router = APIRouter(
    prefix="/capabilities/v1/mcp/servers",
    tags=["plugin-mcp"],
    route_class=PluginRoute,
)


class _RequestTooLarge(Exception):
    pass


async def _read_bounded(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise _RequestTooLarge
        body.extend(chunk)
    return bytes(body)


def _json_response(payload: dict[str, Any]) -> Response:
    try:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        content = json.dumps(_handler.internal_error_response(), separators=(",", ":"))
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{server_id}", include_in_schema=False)
async def mcp_jsonrpc(
    server_id: str,
    request: Request,
    claims: PluginClaimsDep,
) -> Response:
    """Serve one MCP server; auth/revocation run before body processing."""
    try:
        body = await _read_bounded(request, _handler.max_request_bytes)
    except _RequestTooLarge:
        return _json_response(_handler.oversized_request_response())
    except Exception:
        return _json_response(_handler.internal_error_response())

    discovery = getattr(request.app.state, "plugin_mcp_discovery", None)
    payload = await _handler.handle(
        body,
        discovery=discovery,
        server_id=server_id,
        claims=claims,
    )
    if payload is None:
        # Streamable HTTP accepts a notification without a JSON-RPC response.
        return Response(status_code=202, headers={"Cache-Control": "no-store"})
    return _json_response(payload)


__all__ = ["mcp_jsonrpc", "router"]
