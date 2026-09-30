"""DownloadService — presigned redirect or inline stream for local dev."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.alias_binding_service import AliasBindingService
from prodavan.application.content.alias_service import AliasService
from prodavan.application.content.asset_service import AssetService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentAssetRow, ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    url: str | None
    storage_key: str | None
    content_type: str | None


class DownloadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._aliases = AliasService(session)
        self._bindings = AliasBindingService(session)
        self._assets = AssetService(session)
        self._acl = AccessPolicyService(session)

    async def _target_for_version(
        self,
        *,
        asset: ContentAssetRow,
        ver: ContentBlobVersionRow,
        ttl_seconds: int,
    ) -> DownloadTarget:
        store = ensure_file_store()
        url = await store.presign_get(ver.storage_key, ttl_seconds=ttl_seconds)
        if url.startswith("file://"):
            return DownloadTarget(url=None, storage_key=ver.storage_key, content_type=asset.mime)
        # storage_key stays set: proxy downloads (``?proxy=1``) and inline
        # reads fetch by key even on the S3 backend, while the presigned url
        # serves the default 302 flow.
        return DownloadTarget(
            url=url, storage_key=ver.storage_key, content_type=asset.mime
        )

    async def resolve_alias_slug(
        self,
        *,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        ttl_seconds: int = 900,
    ) -> DownloadTarget:
        alias = await self._aliases.get_by_slug(slug)
        await self._acl.require_read_alias(principal=principal, employee=employee, alias=alias)
        binding = await self._bindings.get_current(alias.id)
        if binding is None:
            raise AppError(
                code="NOT_READY",
                title="Not Ready",
                status=404,
                detail="alias has no bound asset",
            )
        asset = await self._session.get(ContentAssetRow, binding.asset_id)
        if asset is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_read_asset(principal=principal, employee=employee, asset=asset)
        ver = await self._assets.resolve_blob_version(
            binding.asset_id,
            blob_version_id=binding.blob_version_id,
        )
        return await self._target_for_version(asset=asset, ver=ver, ttl_seconds=ttl_seconds)

    async def presign_asset(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        blob_version_id: str | None = None,
        ttl_seconds: int = 900,
    ) -> DownloadTarget:
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_read_asset(principal=principal, employee=employee, asset=asset)
        ver = await self._assets.resolve_blob_version(asset_id, blob_version_id=blob_version_id)
        return await self._target_for_version(asset=asset, ver=ver, ttl_seconds=ttl_seconds)

    async def read_asset_bytes(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        blob_version_id: str | None = None,
    ) -> tuple[bytes, str]:
        target = await self.presign_asset(
            asset_id=asset_id,
            principal=principal,
            employee=employee,
            blob_version_id=blob_version_id,
        )
        if target.storage_key is None:
            raise AppError(
                code="NOT_IMPLEMENTED",
                title="Not Implemented",
                status=501,
                detail="inline read requires local backend",
            )
        store = ensure_file_store()
        raw = await store.get_bytes(target.storage_key)
        return raw, target.content_type or "application/octet-stream"
