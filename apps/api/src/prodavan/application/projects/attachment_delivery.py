"""Deliver chat attachments into the agent prompt and/or live Pod workspace."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.tabular_json import (
    is_tabular_filename,
    records_to_json_bytes,
    tabular_bytes_to_json,
)
from prodavan.application.pod_service.adapters.k8s.workspace_exec_cmd import build_workspace_fs_command
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.config.settings import settings
from prodavan.core.infra.object_keys import (
    inbox_object_key,
    parse_content_asset_ref,
    parse_storage_ref,
)
from prodavan.domain.identity import Principal
from prodavan.domain.projects import CHAT_INLINE_TABULAR_ROW_LIMIT
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow, ProjectRow

logger = logging.getLogger(__name__)

DeliveryKind = Literal["inline_json", "workspace_file"]


@dataclass(frozen=True, slots=True)
class DeliveredAttachment:
    filename: str
    storage_ref: str
    kind: DeliveryKind
    workspace_path: str | None
    row_count: int | None
    records: list[dict[str, str]] | None
    note: str

    def ui_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "filename": self.filename,
            "storage_ref": self.storage_ref,
            "kind": self.kind,
            "note": self.note,
        }
        if self.workspace_path:
            out["workspace_path"] = self.workspace_path
        if self.row_count is not None:
            out["row_count"] = self.row_count
        if self.kind == "inline_json" and self.records is not None:
            out["inline_json"] = self.records
        return out


@dataclass(frozen=True, slots=True)
class AttachmentDeliveryResult:
    items: tuple[DeliveredAttachment, ...]
    agent_message: str
    display_text: str

    def ui_attachments(self) -> list[dict[str, Any]]:
        return [item.ui_dict() for item in self.items]


def compose_agent_message(*, user_text: str, items: list[DeliveredAttachment]) -> str:
    """Build the bridge/adapter prompt: attachment notices + optional inline JSON."""
    trimmed = (user_text or "").strip()
    if not items:
        return trimmed

    sections: list[str] = []
    for item in items:
        if item.kind == "inline_json" and item.records is not None:
            payload = records_to_json_bytes(item.records, indent=2).decode("utf-8")
            sections.append(
                "[Attachment: {name} — converted to JSON, {n} row(s) inlined below]\n"
                "```json\n{payload}\n```".format(
                    name=item.filename,
                    n=item.row_count or len(item.records),
                    payload=payload,
                )
            )
        elif item.workspace_path:
            sections.append(
                "[Attachment added: {name} → /workspace/{path}]".format(
                    name=item.filename,
                    path=item.workspace_path,
                )
            )
            if item.note and "converted" in item.note.lower():
                sections.append(f"({item.note})")
        else:
            sections.append(f"[Attachment: {item.filename}] {item.note}".strip())

    prefix = "\n\n".join(sections)
    if trimmed:
        return f"{prefix}\n\n{trimmed}"
    return prefix


def compose_display_text(*, user_text: str, items: list[DeliveredAttachment]) -> str:
    """User-visible bubble text — never dump inline JSON here."""
    trimmed = (user_text or "").strip()
    if trimmed:
        return trimmed
    if not items:
        return ""
    if len(items) == 1:
        return f"Вложение: {items[0].filename}"
    names = ", ".join(i.filename for i in items)
    return f"Вложения: {names}"


class AttachmentDeliveryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load_raw(
        self,
        *,
        project_id: str,
        storage_ref: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> tuple[ProjectAttachmentRow, bytes]:
        q = await self._session.execute(
            select(ProjectAttachmentRow).where(
                ProjectAttachmentRow.project_id == project_id,
                or_(
                    ProjectAttachmentRow.storage_ref == storage_ref,
                    ProjectAttachmentRow.id == storage_ref,
                ),
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            raise LookupError(storage_ref)
        if row.content_asset_id or row.storage_ref.startswith("content://"):
            from prodavan.application.content.download_service import DownloadService

            asset_id = row.content_asset_id or parse_content_asset_ref(row.storage_ref)
            raw, _ctype = await DownloadService(self._session).read_asset_bytes(
                asset_id=asset_id,
                principal=principal,
                employee=employee,
            )
            return row, raw

        store = ensure_file_store()
        try:
            key = parse_storage_ref(row.storage_ref)
        except ValueError:
            project = await self._session.get(ProjectRow, project_id)
            key = inbox_object_key(
                workspace_key=(project.workspace_key if project else "unknown"),
                filename=row.filename,
            )
        raw = await store.get_bytes(key)
        return row, raw

    async def _put_inbox_bytes(
        self,
        *,
        workspace_key: str,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> str:
        key = inbox_object_key(workspace_key=workspace_key, filename=filename)
        store = ensure_file_store()
        await store.put_bytes(key, data, content_type=content_type)
        return f"inbox/{Path(filename).name}"

    async def _write_pod_file(
        self,
        *,
        runtime_ref: str | None,
        relative_path: str,
        data: bytes,
    ) -> None:
        if not runtime_ref or (settings.pod_runtime_mode or "stub").strip().lower() != "k8s":
            return
        from prodavan.core.infra.k8s_manager import get_k8s_manager
        from prodavan.infrastructure.k8s.sandbox.exec import exec_in_pod

        mgr = get_k8s_manager()
        if mgr is None or mgr.client is None:
            logger.warning("attachment pod write skipped: no k8s client path=%s", relative_path)
            return
        rel = normalize_workspace_path(relative_path)
        if not rel:
            return
        client = mgr.client
        result = await exec_in_pod(
            auth=client.auth,
            namespace=client.namespace,
            pod_name=runtime_ref,
            command=build_workspace_fs_command(["write", rel]),
            stdin_data=data,
            timeout=120.0,
        )
        if result.exit_code not in (0, None):
            err = result.stderr.decode("utf-8", errors="replace")[:500]
            logger.warning("attachment pod write failed path=%s err=%s", rel, err)
            raise RuntimeError(err or "pod write failed")

    async def deliver_for_send(
        self,
        *,
        project_id: str,
        workspace_key: str,
        storage_refs: list[str],
        user_text: str,
        runtime_ref: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> AttachmentDeliveryResult:
        items: list[DeliveredAttachment] = []
        for ref in storage_refs:
            row, raw = await self._load_raw(
                project_id=project_id,
                storage_ref=ref,
                principal=principal,
                employee=employee,
            )
            filename = row.filename or Path(ref).name or "attachment.bin"
            delivered = await self._prepare_one(
                filename=filename,
                storage_ref=row.storage_ref,
                raw=raw,
                workspace_key=workspace_key,
                runtime_ref=runtime_ref,
            )
            items.append(delivered)

        return AttachmentDeliveryResult(
            items=tuple(items),
            agent_message=compose_agent_message(user_text=user_text, items=items),
            display_text=compose_display_text(user_text=user_text, items=items),
        )

    async def _prepare_one(
        self,
        *,
        filename: str,
        storage_ref: str,
        raw: bytes,
        workspace_key: str,
        runtime_ref: str | None,
    ) -> DeliveredAttachment:
        if is_tabular_filename(filename):
            try:
                parsed = tabular_bytes_to_json(raw, filename=filename)
            except ValueError as exc:
                rel = f"inbox/{Path(filename).name}"
                await self._put_inbox_bytes(
                    workspace_key=workspace_key,
                    filename=Path(filename).name,
                    data=raw,
                    content_type="application/octet-stream",
                )
                try:
                    await self._write_pod_file(runtime_ref=runtime_ref, relative_path=rel, data=raw)
                except Exception:
                    logger.exception("pod write failed for %s", rel)
                return DeliveredAttachment(
                    filename=filename,
                    storage_ref=storage_ref,
                    kind="workspace_file",
                    workspace_path=rel,
                    row_count=None,
                    records=None,
                    note=f"could not convert tabular file ({exc}); raw file at /workspace/{rel}",
                )

            if parsed.row_count <= CHAT_INLINE_TABULAR_ROW_LIMIT:
                return DeliveredAttachment(
                    filename=filename,
                    storage_ref=storage_ref,
                    kind="inline_json",
                    workspace_path=None,
                    row_count=parsed.row_count,
                    records=parsed.records,
                    note=f"converted {parsed.source_format} → JSON, inlined ({parsed.row_count} rows)",
                )

            json_name = f"{Path(filename).stem}.json"
            rel = f"inbox/{json_name}"
            json_bytes = records_to_json_bytes(parsed.records, indent=2)
            await self._put_inbox_bytes(
                workspace_key=workspace_key,
                filename=json_name,
                data=json_bytes,
                content_type="application/json",
            )
            try:
                await self._write_pod_file(runtime_ref=runtime_ref, relative_path=rel, data=json_bytes)
            except Exception:
                logger.exception("pod write failed for %s", rel)
            return DeliveredAttachment(
                filename=filename,
                storage_ref=storage_ref,
                kind="workspace_file",
                workspace_path=rel,
                row_count=parsed.row_count,
                records=None,
                note=f"converted {parsed.source_format} → JSON ({parsed.row_count} rows) at /workspace/{rel}",
            )

        safe_name = Path(filename).name
        rel = f"inbox/{safe_name}"
        await self._put_inbox_bytes(
            workspace_key=workspace_key,
            filename=safe_name,
            data=raw,
            content_type=row_content_type_guess(filename),
        )
        try:
            await self._write_pod_file(runtime_ref=runtime_ref, relative_path=rel, data=raw)
        except Exception:
            logger.exception("pod write failed for %s", rel)
        return DeliveredAttachment(
            filename=filename,
            storage_ref=storage_ref,
            kind="workspace_file",
            workspace_path=rel,
            row_count=None,
            records=None,
            note=f"file available at /workspace/{rel}",
        )


def row_content_type_guess(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".zip": "application/zip",
    }.get(ext, "application/octet-stream")
