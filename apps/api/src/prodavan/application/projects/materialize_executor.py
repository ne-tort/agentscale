"""Execute materialize ops — write workspace files from cabinet data."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.materialize_planner import MaterializeOp
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter

logger = logging.getLogger(__name__)


class MaterializeExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def execute(
        self,
        *,
        writer: WorkspaceLayoutWriter,
        cabinet_id: str,
        ops: list[MaterializeOp],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        written: list[str] = []
        mcp_packages: list[dict[str, Any]] = []
        for op in ops:
            try:
                if op.format == "raw":
                    path = await self._write_raw(writer, op)
                    if path:
                        written.append(path)
                elif op.format == "copy_blob":
                    path = await self._write_copy_blob(writer, op)
                    if path:
                        written.append(path)
                elif op.format == "mcp_package":
                    pkg = await self._write_mcp_package(writer, op)
                    if pkg:
                        mcp_packages.append(pkg)
                        written.append(f"packages/{pkg['name']}")
            except Exception:
                logger.exception("materialize op failed rule=%s path=%s", op.rule_id, op.workspace_path)
        if mcp_packages:
            from prodavan.application.mcp.materialize_adapter import McpMaterializeAdapter

            adapter = McpMaterializeAdapter(self._session)
            await adapter.patch_mcp_config(writer=writer, cabinet_id=cabinet_id, packages=mcp_packages)
        return written, mcp_packages

    async def _write_raw(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        if not op.workspace_path or op.row_body is None:
            return None
        field = op.field or "body_md"
        val = op.row_body.get(field)
        if val is None and field == "body_md":
            val = ""
        if val is None:
            return None
        text = val if isinstance(val, str) else str(val)
        writer.write_text_file(relative_path=op.workspace_path, text=text)
        return op.workspace_path

    async def _write_copy_blob(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        ref = op.file_ref or (op.row_body or {}).get(op.field or "file_ref")
        if not isinstance(ref, dict) or not op.workspace_path:
            return None
        raw = await self._load_file_ref(ref)
        if raw is None:
            return None
        writer.write_bytes_file(relative_path=op.workspace_path, data=raw)
        return op.workspace_path

    async def _write_mcp_package(
        self, writer: WorkspaceLayoutWriter, op: MaterializeOp
    ) -> dict[str, Any] | None:
        ref = op.file_ref or (op.row_body or {}).get(op.field or "file_ref")
        if not isinstance(ref, dict):
            return None
        raw = await self._load_file_ref(ref)
        if raw is None:
            return None
        from prodavan.application.mcp.materialize_adapter import McpMaterializeAdapter
        from prodavan.application.mcp.package_validator import McpPackageValidator

        manifest = McpPackageValidator().validate_zip(raw)
        name = op.mcp_package_name or manifest.get("name") or "package"
        adapter = McpMaterializeAdapter(self._session)
        return await adapter.extract_to_workspace(writer=writer, package_name=name, zip_bytes=raw, manifest=manifest)

    async def _load_file_ref(self, ref: dict[str, Any]) -> bytes | None:
        storage_key = ref.get("storage_key")
        if isinstance(storage_key, str) and storage_key:
            try:
                return ensure_file_store().get_bytes_sync(storage_key)
            except FileNotFoundError:
                pass
        version_id = ref.get("version_id") or ref.get("blob_version_id")
        if isinstance(version_id, str) and version_id:
            ver = await self._session.get(ContentBlobVersionRow, version_id)
            if ver is not None:
                try:
                    return ensure_file_store().get_bytes_sync(ver.storage_key)
                except FileNotFoundError:
                    return None
        return None
