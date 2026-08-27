"""AssetService — logical files + blob versions."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.domain.content.types import ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.files.keys import new_blob_key
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentAssetRow, ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def _asset_public(row: ContentAssetRow, *, latest_version: int | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "visibility": row.visibility,
        "mime": row.mime,
        "title": row.title,
        "tags": row.tags or [],
        "created_by": row.created_by,
        "latest_version": latest_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _version_public(row: ContentBlobVersionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "asset_id": row.asset_id,
        "version": row.version,
        "storage_key": row.storage_key,
        "size": row.size,
        "sha256": row.sha256,
        "etag": row.etag,
        "object_metadata": row.object_metadata or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class AssetService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._acl = AccessPolicyService(session)

    async def create(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        owner_company_id: str | None,
        visibility: str = ContentVisibility.COMPANY,
        mime: str | None = None,
        title: str | None = None,
        tags: list | None = None,
    ) -> dict[str, Any]:
        if employee is None and not principal.is_platform_admin:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        scope = OwnerScope.COMPANY if owner_company_id else OwnerScope.PLATFORM
        row = ContentAssetRow(
            owner_scope=scope,
            owner_company_id=owner_company_id,
            visibility=visibility,
            mime=mime,
            title=title,
            tags=tags or [],
            created_by=employee.id if employee else None,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return _asset_public(row, latest_version=None)

    async def get(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_read_asset(principal=principal, employee=employee, asset=row)
        latest = await self._latest_version_number(asset_id)
        return _asset_public(row, latest_version=latest)

    async def list_for_company(
        self,
        *,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ContentAssetRow)
            .where(ContentAssetRow.owner_company_id == company_id)
            .order_by(ContentAssetRow.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        out: list[dict[str, Any]] = []
        for row in q.scalars().all():
            if await self._acl.can_read_asset(principal=principal, employee=employee, asset=row):
                latest = await self._latest_version_number(row.id)
                out.append(_asset_public(row, latest_version=latest))
        return out

    async def patch(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        title: str | None = None,
        tags: list | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_write_asset(principal=principal, employee=employee, asset=row)
        if title is not None:
            row.title = title
        if tags is not None:
            row.tags = tags
        if visibility is not None:
            row.visibility = visibility
        await self._session.commit()
        await self._session.refresh(row)
        latest = await self._latest_version_number(asset_id)
        return _asset_public(row, latest_version=latest)

    async def delete(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_write_asset(principal=principal, employee=employee, asset=row)
        public = _asset_public(row)
        q = await self._session.execute(
            select(ContentBlobVersionRow.storage_key).where(ContentBlobVersionRow.asset_id == asset_id)
        )
        keys = [k for k in q.scalars().all() if k]
        store = ensure_file_store()
        for key in keys:
            await store.delete(key)
        await self._session.delete(row)
        await self._session.commit()
        return {"deleted": True, **public}

    async def begin_version_upload(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        mime: str | None = None,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_write_asset(principal=principal, employee=employee, asset=row)
        version = await self._next_version_number(asset_id)
        storage_key = new_blob_key()
        store = ensure_file_store()
        upload_url = await store.presign_put(storage_key, ttl_seconds=ttl_seconds, content_type=mime)
        ver_row = ContentBlobVersionRow(
            asset_id=asset_id,
            version=version,
            storage_key=storage_key,
            size=0,
        )
        self._session.add(ver_row)
        if mime:
            row.mime = mime
        await self._session.commit()
        await self._session.refresh(ver_row)
        return {
            "version": _version_public(ver_row),
            "upload_url": upload_url,
            "method": "PUT",
        }

    async def finalize_version(
        self,
        *,
        asset_id: str,
        version_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAssetRow, asset_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_write_asset(principal=principal, employee=employee, asset=row)
        ver = await self._session.get(ContentBlobVersionRow, version_id)
        if ver is None or ver.asset_id != asset_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="version not found")
        store = ensure_file_store()
        try:
            head = await store.head(ver.storage_key)
        except FileNotFoundError as exc:
            raise AppError(
                code="NOT_READY",
                title="Not Ready",
                status=422,
                detail="blob not uploaded yet",
            ) from exc
        ver.size = head.size
        ver.etag = head.etag
        if head.content_type and not row.mime:
            row.mime = head.content_type
        try:
            raw = await store.get_bytes(ver.storage_key)
            ver.sha256 = hashlib.sha256(raw).hexdigest()
        except FileNotFoundError:
            pass
        await self._session.commit()
        await self._session.refresh(ver)
        return _version_public(ver)

    async def resolve_blob_version(
        self,
        asset_id: str,
        *,
        blob_version_id: str | None = None,
    ) -> ContentBlobVersionRow:
        if blob_version_id:
            ver = await self._session.get(ContentBlobVersionRow, blob_version_id)
            if ver is None or ver.asset_id != asset_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="version not found")
            return ver
        q = await self._session.execute(
            select(ContentBlobVersionRow)
            .where(ContentBlobVersionRow.asset_id == asset_id)
            .order_by(ContentBlobVersionRow.version.desc())
            .limit(1)
        )
        ver = q.scalar_one_or_none()
        if ver is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="no blob version")
        return ver

    async def _latest_version_number(self, asset_id: str) -> int | None:
        q = await self._session.execute(
            select(func.max(ContentBlobVersionRow.version)).where(
                ContentBlobVersionRow.asset_id == asset_id
            )
        )
        val = q.scalar_one_or_none()
        return int(val) if val is not None else None

    async def _next_version_number(self, asset_id: str) -> int:
        latest = await self._latest_version_number(asset_id)
        return (latest or 0) + 1
