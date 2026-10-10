"""Materialize first-party prodavan-modules MCP into project workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prodavan.application.mcp.bridge_env import platform_mcp_bridge_env_refs
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

PACKAGE_NAME = "prodavan-modules"
PACKAGE_VERSION = "1.0.1"

_TOOL_NAMES = (
    "modules_list",
    "module_meta_list",
    "module_meta_get",
    "module_meta_put",
    "module_data_list",
    "module_data_create",
    "module_data_update",
    "module_data_delete",
    "module_action_invoke",
)


def platform_modules_mcp_package() -> dict[str, Any]:
    return {
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "command": "python",
        "args": [f"packages/{PACKAGE_NAME}/server.py"],
        "tools": list(_TOOL_NAMES),
        "env": platform_mcp_bridge_env_refs(),
    }


def materialize_platform_modules_mcp(writer: WorkspaceLayoutWriter) -> dict[str, Any]:
    """Copy stdio MCP server into workspace packages/ and return mcp.json entry."""
    src = Path(__file__).resolve().parent / "prodavan_modules_mcp" / "server.py"
    text = src.read_text(encoding="utf-8")
    writer.write_text_file(
        relative_path=f"packages/{PACKAGE_NAME}/server.py",
        text=text,
    )
    return platform_modules_mcp_package()


def merge_platform_modules_mcp(
    packages: list[dict[str, Any]],
    *,
    platform_pkg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ensure platform package is present (first) and unique by name."""
    others = [p for p in packages if isinstance(p, dict) and p.get("name") != PACKAGE_NAME]
    return [platform_pkg, *others]
