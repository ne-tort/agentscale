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
        safe = package_name.replace("/", "_").replace("\\", "_").strip() or "package"
        manifest_name = str(manifest.get("name") or "").strip()
        if manifest_name:
            safe = manifest_name.replace("/", "_").replace("\\", "_")
        names = writer.extract_packages([(safe, zip_bytes)])
        entry = manifest.get("entry") or {}
        args = list(entry.get("args") or [])
        # Prefer workspace-relative entry under packages/{name}/ when missing.
        if not args and entry.get("command") == "python":
            args = [f"packages/{safe}/server.py"]
        extracted_ok = safe in names
        entry_rel = None
        for arg in args:
            text = str(arg)
            prefix = f"packages/{safe}/"
            if text.startswith(prefix):
                entry_rel = text[len(prefix) :]
                break
            if text.startswith(f"${{WORKSPACE}}/{prefix}"):
                entry_rel = text.split(prefix, 1)[-1]
                break
        if entry_rel is None:
            entry_rel = "server.py"
        if extracted_ok and writer.read_bytes_file(f"packages/{safe}/{entry_rel}") is None:
            extracted_ok = False
        return {
            "name": safe,
            "version": manifest.get("version") or "1.0.0",
            "command": entry.get("command"),
            "args": args,
            "tools": manifest.get("tools") or [],
            "extracted": extracted_ok,
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
