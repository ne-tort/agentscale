"""Execute materialize ops — write workspace files from cabinet data."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.materialize_planner import MaterializeOp
from prodavan.application.projects.template_substitute import substitute_blanking_missing
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
                elif op.format == "json_rows":
                    path = self._write_json_rows(writer, op)
                    if path:
                        written.append(path)
                elif op.format == "json_single":
                    path = self._write_json_single(writer, op)
                    if path:
                        written.append(path)
                elif op.format == "template":
                    path = self._write_template(writer, op)
                    if path:
                        written.append(path)
                elif op.format == "merge_mapped_sqlite":
                    path = await self._write_merge_mapped_sqlite(writer, op)
                    if path:
                        written.append(path)
            except Exception:
                logger.exception("materialize op failed rule=%s path=%s", op.rule_id, op.workspace_path)
        if mcp_packages:
            from prodavan.application.mcp.materialize_adapter import McpMaterializeAdapter

            adapter = McpMaterializeAdapter(self._session)
            await adapter.patch_mcp_config(writer=writer, cabinet_id=cabinet_id, packages=mcp_packages)
        return written, mcp_packages

    async def _write_raw(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        if not op.workspace_path:
            return None
        if op.static_value is not None and op.row_body is None:
            writer.write_text_file(relative_path=op.workspace_path, text=op.static_value)
            return op.workspace_path
        if op.row_body is None:
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
        if _should_skip_copy_blob(writer, op.workspace_path, ref):
            return op.workspace_path
        raw = await self._load_file_ref(ref)
        if raw is None:
            return None
        writer.write_bytes_file(relative_path=op.workspace_path, data=raw)
        return op.workspace_path

    def _write_json_rows(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        if not op.workspace_path or not op.rows_bodies:
            return None
        payload = [{k: v for k, v in row.items() if k != "row_id"} for row in op.rows_bodies]
        writer.write_text_file(
            relative_path=op.workspace_path,
            text=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        return op.workspace_path

    def _write_json_single(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        if not op.workspace_path or op.row_body is None:
            return None
        body = {k: v for k, v in op.row_body.items() if k != "row_id"}
        writer.write_text_file(
            relative_path=op.workspace_path,
            text=json.dumps(body, ensure_ascii=False, indent=2),
        )
        return op.workspace_path

    def _write_template(self, writer: WorkspaceLayoutWriter, op: MaterializeOp) -> str | None:
        if not op.workspace_path:
            return None
        template = op.template_text or ""
        ctx = op.row_body or {}
        if op.static_value is not None and not template:
            template = op.static_value
        text = _render_template(template, ctx)
        writer.write_text_file(relative_path=op.workspace_path, text=text)
        return op.workspace_path

    async def _write_merge_mapped_sqlite(
        self, writer: WorkspaceLayoutWriter, op: MaterializeOp
    ) -> str | None:
        if not op.workspace_path:
            return None
        from prodavan.application.content.merge_mapped_sqlite import merge_mapped_sqlite_bytes

        params = op.merge_params or {}
        provenance = params.get("provenance") if isinstance(params.get("provenance"), dict) else {}
        schema = params.get("schema")
        schema_list = [str(c) for c in schema] if isinstance(schema, list) and schema else None
        required = params.get("required_map_keys")
        required_list = (
            [str(c) for c in required] if isinstance(required, list) and required else None
        )

        async def _load(ref: dict[str, Any]) -> bytes | None:
            return await self._load_file_ref(ref)

        # Preload artifacts synchronously for the pure merge helper.
        sources: list[dict[str, Any]] = []
        for body in op.rows_bodies or []:
            item = dict(body)
            artifact_field = str(params.get("artifact_field") or "artifact_ref")
            ref = item.get(artifact_field)
            if isinstance(ref, dict):
                raw = await _load(ref)
                if raw is not None:
                    item["_artifact_bytes"] = raw
            sources.append(item)

        blob, _count, _content_sha = merge_mapped_sqlite_bytes(
            sources,
            schema=schema_list,
            required_map_keys=required_list,
            artifact_field=str(params.get("artifact_field") or "artifact_ref"),
            map_field=str(params.get("map_field") or "column_map"),
            provenance_target=str(provenance.get("target") or "source_catalog"),
            provenance_from=str(provenance.get("from") or "name"),
        )
        existing = writer.read_bytes_file(op.workspace_path)
        if existing is not None and hashlib.sha256(existing).hexdigest() == hashlib.sha256(blob).hexdigest():
            return op.workspace_path
        writer.write_bytes_file(relative_path=op.workspace_path, data=blob)
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
        # Manifest name is SoT for packages/{name}/ (row label may be a UI title).
        name = str(manifest.get("name") or op.mcp_package_name or "package").strip() or "package"
        adapter = McpMaterializeAdapter(self._session)
        return await adapter.extract_to_workspace(
            writer=writer, package_name=name, zip_bytes=raw, manifest=manifest
        )

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


def _should_skip_copy_blob(writer: WorkspaceLayoutWriter, workspace_path: str, ref: dict[str, Any]) -> bool:
    """P-META-FILE-05: skip rewrite when workspace file matches FileRef sha256."""
    expected = ref.get("sha256")
    if not isinstance(expected, str) or not expected.strip():
        return False
    existing = writer.read_bytes_file(workspace_path)
    if existing is None:
        return False
    return hashlib.sha256(existing).hexdigest() == expected.strip()


def _render_template(template: str, ctx: dict[str, Any]) -> str:
    """Render ``{{var}}`` placeholders in file-content templates.

    Shares the single substitution dialect with the planner
    (``template_substitute``) so there is one regex and one placeholder
    contract (audit META-P1c). For file content, missing values blank to ""
    instead of leaving the placeholder verbatim (a literal ``{{var}}`` inside
    a rendered file would be confusing).
    """
    blanked = {k: ("" if v is None else str(v)) for k, v in ctx.items()}
    return substitute_blanking_missing(template, blanked)
