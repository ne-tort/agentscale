"""Materialize MCP packages into project workspace + mcp.json."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


class McpMaterializeAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def extract_to_workspace(
        self,
        *,
        writer: WorkspaceLayoutWriter,
        package_name: str,
        zip_bytes: bytes,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        safe = package_name.replace("/", "_").replace("\\", "_")
        names = writer.extract_packages([(safe, zip_bytes)])
        entry = manifest.get("entry") or {}
        return {
            "name": safe,
            "version": manifest.get("version") or "1.0.0",
            "command": entry.get("command"),
            "args": entry.get("args") or [],
            "tools": manifest.get("tools") or [],
            "extracted": safe in names,
        }

    async def patch_mcp_config(
        self,
        *,
        writer: WorkspaceLayoutWriter,
        cabinet_id: str,
        packages: list[dict[str, Any]],
    ) -> None:
        mcp_packages = []
        for pkg in packages:
            mcp_packages.append(
                {
                    "name": pkg.get("name"),
                    "version": pkg.get("version"),
                    "command": pkg.get("command"),
                    "args": pkg.get("args") or [],
                    "tools": pkg.get("tools") or [],
                }
            )
        writer.write_mcp_config(cabinet_id=cabinet_id, packages=mcp_packages)
