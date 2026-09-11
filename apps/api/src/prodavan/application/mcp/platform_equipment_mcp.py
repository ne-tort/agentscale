"""Materialize first-party prodavan-equipment MCP into project workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

PACKAGE_NAME = "prodavan-equipment"
PACKAGE_VERSION = "1.0.0"

_TOOL_NAMES = (
    "equipment_catalog_sources",
    "equipment_catalog_search",
    "request_lines_list",
    "request_lines_get",
    "request_lines_upsert",
    "found_offers_list",
    "found_offers_get",
    "found_offers_upsert",
)


def platform_equipment_mcp_package() -> dict[str, Any]:
    return {
        "name": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "command": "python",
        "args": [f"packages/{PACKAGE_NAME}/server.py"],
        "tools": list(_TOOL_NAMES),
    }


def materialize_platform_equipment_mcp(writer: WorkspaceLayoutWriter) -> dict[str, Any]:
    """Copy stdio MCP server + search helper into workspace packages/."""
    mcp_dir = Path(__file__).resolve().parent
    search_src = mcp_dir.parent / "modules" / "equipment_catalog_search.py"
    server_src = mcp_dir / "prodavan_equipment_mcp" / "server.py"
    writer.write_text_file(
        relative_path=f"packages/{PACKAGE_NAME}/equipment_catalog_search.py",
        text=search_src.read_text(encoding="utf-8"),
    )
    writer.write_text_file(
        relative_path=f"packages/{PACKAGE_NAME}/server.py",
        text=server_src.read_text(encoding="utf-8"),
    )
    return platform_equipment_mcp_package()


def merge_platform_equipment_mcp(
    packages: list[dict[str, Any]],
    *,
    platform_pkg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ensure equipment package is present and unique by name."""
    others = [p for p in packages if isinstance(p, dict) and p.get("name") != PACKAGE_NAME]
    # Keep modules first if present, then equipment, then rest
    modules = [p for p in others if p.get("name") == "prodavan-modules"]
    rest = [p for p in others if p.get("name") != "prodavan-modules"]
    return [*modules, platform_pkg, *rest]
