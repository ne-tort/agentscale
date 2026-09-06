"""UploadService — direct blob upload via FileStore (non-presign path)."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.asset_service import AssetService
from prodavan.domain.content.types import ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.files.keys import new_blob_key
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentAssetLinkRow, ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class UploadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._assets = AssetService(session)

    async def upload_bytes_as_asset(
        self,
        *,
        data: bytes,
        owner_company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        mime: str | None,
        title: str | None,
        link_kind: str | None = None,
        link_id: str | None = None,
    ) -> tuple[str, str]:
        """Create asset + v1 blob; optional domain link. Returns (asset_id, version_id)."""
        asset = await self._assets.create(
            principal=principal,
            employee=employee,
            owner_company_id=owner_company_id,
            visibility=ContentVisibility.COMPANY,
            mime=mime,
            title=title,
        )
        asset_id = asset["id"]
        storage_key = new_blob_key()
        store = ensure_file_store()
        await store.put_bytes(storage_key, data, content_type=mime)
        ver_row = ContentBlobVersionRow(
            asset_id=asset_id,
            version=1,
            storage_key=storage_key,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        self._session.add(ver_row)
        if link_kind and link_id:
            await self._upsert_asset_link(
                asset_id=asset_id,
                link_kind=link_kind,
                link_id=link_id,
            )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail="content asset link already exists",
            ) from exc
        await self._session.refresh(ver_row)
        return asset_id, ver_row.id

    async def _upsert_asset_link(
        self,
        *,
        asset_id: str,
        link_kind: str,
        link_id: str,
    ) -> None:
        """One asset per (link_kind, link_id) — replace on re-upload/re-index."""
        q = await self._session.execute(
            select(ContentAssetLinkRow).where(
                ContentAssetLinkRow.link_kind == link_kind,
                ContentAssetLinkRow.link_id == link_id,
            )
        )
        existing = q.scalar_one_or_none()
        if existing is not None:
            existing.asset_id = asset_id
            return
        self._session.add(
            ContentAssetLinkRow(
                asset_id=asset_id,
                link_kind=link_kind,
                link_id=link_id,
            )
        )

    async def link_asset(
        self,
        *,
        asset_id: str,
        link_kind: str,
        link_id: str,
    ) -> None:
        await self._upsert_asset_link(
            asset_id=asset_id,
            link_kind=link_kind,
            link_id=link_id,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail="content asset link already exists",
            ) from exc
