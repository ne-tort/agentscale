"""Project chat attachments (L07)."""

from __future__ import annotations

import base64
import mimetypes

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.projects.access import ProjectAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ATTACHMENT_MAX_BYTES
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


class ProjectAttachmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessService(session)

    async def upload_base64(
        self,
        *,
        project_id: str,
        filename: str,
        content_base64: str,
        content_type: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        project = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="invalid content_base64",
            ) from exc
        if len(raw) > ATTACHMENT_MAX_BYTES:
            raise AppError(
                code="ATTACHMENT_TOO_LARGE",
                title="Attachment too large",
                status=413,
                detail=f"max {ATTACHMENT_MAX_BYTES} bytes",
            )
        safe_name = filename.strip() or "attachment.bin"
        guessed = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        writer = WorkspaceLayoutWriter(workspace_key=project.workspace_key)
        path = writer.store_inbox_attachment(filename=safe_name, raw=raw)
        storage_ref = f"file://projects/{project.workspace_key}/workspace/inbox/{path.name}"
        row = ProjectAttachmentRow(
            project_id=project_id,
            filename=path.name,
            content_type=guessed,
            size_bytes=len(raw),
            storage_ref=storage_ref,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return {
            "id": row.id,
            "filename": row.filename,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "storage_ref": row.storage_ref,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
