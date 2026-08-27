"""Project chat attachments (L07)."""

from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.company_service import AdminCompanyService
from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.application.projects.access import ProjectAccessService
from prodavan.config.settings import settings
from prodavan.core.infra.object_keys import (
    content_asset_ref,
    inbox_object_key,
    object_ref,
    parse_content_asset_ref,
    parse_storage_ref,
)
from prodavan.domain.admin import attachment_max_bytes
from prodavan.domain.content.types import AssetLinkKind
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import (
    is_allowed_attachment_filename,
    is_forbidden_attachment_content,
    sniff_attachment_content_type,
)
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow


def _attachment_public(row: ProjectAttachmentRow) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "storage_ref": row.storage_ref,
        "content_asset_id": row.content_asset_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class ProjectAttachmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = ProjectAccessService(session)
        self._companies = AdminCompanyService(session)
        self._subscription = CompanySubscriptionGate(session)

    async def _max_bytes(self, company_id: str) -> int:
        policy = await self._companies.get_agent_policy(company_id)
        return attachment_max_bytes(policy)

    async def list_for_project(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict]:
        await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(ProjectAttachmentRow)
            .where(ProjectAttachmentRow.project_id == project_id)
            .order_by(ProjectAttachmentRow.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        return [_attachment_public(r) for r in q.scalars().all()]

    async def normalize_refs(self, *, project_id: str, refs: list[str]) -> list[str]:
        """Validate refs belong to project; accept id or storage_ref; return storage_refs."""
        if not refs:
            return []
        ordered = list(dict.fromkeys(str(r).strip() for r in refs if str(r).strip()))
        if not ordered:
            return []
        q = await self._session.execute(
            select(ProjectAttachmentRow).where(
                ProjectAttachmentRow.project_id == project_id,
                or_(
                    ProjectAttachmentRow.storage_ref.in_(ordered),
                    ProjectAttachmentRow.id.in_(ordered),
                ),
            )
        )
        by_ref = {row.storage_ref: row for row in q.scalars().all()}
        by_id = {row.id: row for row in by_ref.values()}
        out: list[str] = []
        for ref in ordered:
            row = by_ref.get(ref) or by_id.get(ref)
            if row is None:
                raise AppError(
                    code="ATTACHMENT_NOT_FOUND",
                    title="Attachment not found",
                    status=422,
                    detail=f"attachment ref not found in project: {ref}",
                )
            out.append(row.storage_ref)
        return out

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
        await self._subscription.require_active(project.company_id)
        safe_name = filename.strip() or "attachment.bin"
        if not is_allowed_attachment_filename(safe_name):
            raise AppError(
                code="ATTACHMENT_TYPE_FORBIDDEN",
                title="Attachment type forbidden",
                status=422,
                detail="file extension not allowed for chat attachments",
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
        max_bytes = await self._max_bytes(project.company_id)
        if len(raw) > max_bytes:
            raise AppError(
                code="ATTACHMENT_TOO_LARGE",
                title="Attachment too large",
                status=413,
                detail=f"max {max_bytes} bytes (company policy)",
            )
        if is_forbidden_attachment_content(raw):
            raise AppError(
                code="ATTACHMENT_CONTENT_FORBIDDEN",
                title="Attachment content forbidden",
                status=422,
                detail="executable or binary content not allowed for chat attachments",
            )
        guessed = sniff_attachment_content_type(raw, filename=safe_name, fallback=content_type)

        content_asset_id: str | None = None
        storage_ref: str

        if settings.content_attachments_via_assets:
            from prodavan.application.content.upload_service import UploadService

            upload = UploadService(self._session)
            asset_id, _ = await upload.upload_bytes_as_asset(
                data=raw,
                owner_company_id=project.company_id,
                principal=principal,
                employee=employee,
                mime=guessed,
                title=Path(safe_name).name,
            )
            content_asset_id = asset_id
            storage_ref = content_asset_ref(asset_id)
        else:
            key = inbox_object_key(workspace_key=project.workspace_key, filename=safe_name)
            store = ensure_file_store()
            await store.put_bytes(key, raw, content_type=guessed)
            storage_ref = object_ref(key)

        row = ProjectAttachmentRow(
            project_id=project_id,
            filename=Path(safe_name).name,
            content_type=guessed,
            size_bytes=len(raw),
            storage_ref=storage_ref,
            content_asset_id=content_asset_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)

        if settings.content_attachments_via_assets and content_asset_id:
            await UploadService(self._session).link_asset(
                asset_id=content_asset_id,
                link_kind=AssetLinkKind.PROJECT_ATTACHMENT,
                link_id=row.id,
            )
            await self._session.refresh(row)

        return _attachment_public(row)

    async def delete(
        self,
        *,
        project_id: str,
        attachment_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        project = await self._access.require_access(
            project_id=project_id,
            principal=principal, employee=employee, write=True, allow_paused=True
        )
        row = await self._session.get(ProjectAttachmentRow, attachment_id)
        if row is None or row.project_id != project_id:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="Attachment not found",
            )
        public = _attachment_public(row)
        if row.content_asset_id:
            from prodavan.application.content.asset_service import AssetService

            await AssetService(self._session).delete(
                asset_id=row.content_asset_id,
                principal=principal,
                employee=employee,
            )
            await self._session.delete(row)
            await self._session.commit()
        else:
            store = ensure_file_store()
            try:
                key = parse_storage_ref(row.storage_ref)
            except ValueError:
                key = inbox_object_key(workspace_key=project.workspace_key, filename=row.filename)
            await store.delete(key)
            await self._session.delete(row)
            await self._session.commit()
        return {"deleted": True, **public}

    async def read_content(
        self,
        *,
        project_id: str,
        attachment_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> tuple[bytes, str, str]:
        project = await self._access.require_access(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        row = await self._session.get(ProjectAttachmentRow, attachment_id)
        if row is None or row.project_id != project_id:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="Attachment not found",
            )
        if row.content_asset_id or row.storage_ref.startswith("content://"):
            from prodavan.application.content.download_service import DownloadService

            asset_id = row.content_asset_id or parse_content_asset_ref(row.storage_ref)
            raw, content_type = await DownloadService(self._session).read_asset_bytes(
                asset_id=asset_id,
                principal=principal,
                employee=employee,
            )
            return raw, content_type, row.filename

        store = ensure_file_store()
        try:
            key = parse_storage_ref(row.storage_ref)
        except ValueError:
            key = inbox_object_key(workspace_key=project.workspace_key, filename=row.filename)
        try:
            raw = await store.get_bytes(key)
        except FileNotFoundError as exc:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="attachment blob missing in object store",
            ) from exc
        content_type = row.content_type or "application/octet-stream"
        return raw, content_type, row.filename
