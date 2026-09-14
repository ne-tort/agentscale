"""Env keys platform MCP servers need from the Pod (Bridge API).

Values are written to mcp.json / OpenClaw config as ``${VAR}`` placeholders so
object-store never holds plaintext tokens. Agent-runtime expands them from
process env when spawning stdio MCP (OpenClaw merge + Cursor explicit env).
"""

from __future__ import annotations

# Keys read by prodavan-modules / prodavan-equipment server.py `_env()`.
PLATFORM_MCP_BRIDGE_ENV_KEYS: tuple[str, ...] = (
    "PRODAVAN_API_BASE_URL",
    "PRODAVAN_AUTH_TOKEN",
    "PRODAVAN_PROJECT_ID",
    "PRODAVAN_SESSION_ID",
    "BRIDGE_AUTH_TOKEN",
    "PROJECT_ID",
    "WORKSPACE_ROOT",
)


def platform_mcp_bridge_env_refs() -> dict[str, str]:
    """Placeholder map for mcp.json packages[].env / OpenClaw mcp.servers.*.env."""
    return {key: f"${{{key}}}" for key in PLATFORM_MCP_BRIDGE_ENV_KEYS}
