"""DownloadService — presigned redirect or inline stream for local dev.

Every read resolves the asset through the ACL first, so a download URL is never
issued for an ungranted or foreign-tenant object. A version that has no sealed
digest yet is ``422 NOT_READY`` rather than an empty 200.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.alias_binding_service import AliasBindingService
from prodavan.application.content.alias_service import AliasService
from prodavan.application.content.asset_service import AssetService
from prodavan.application.content.contracts import ContentErrorCode
from prodavan.application.content.scope import (
    ContentActorScope,
    alias_tenant_visible,
    asset_tenant_visible,
    not_found,
    resolve_actor_scope,
)
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
    filename: str | None = None
    sha256: str | None = None
    size: int | None = None


def safe_filename(value: str | None) -> str | None:
    """Strip control characters and path separators from a download filename."""
    if not value:
        return None
    base = str(value).replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in base if 32 <= ord(ch) != 127).strip()
    return cleaned or None


class DownloadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._aliases = AliasService(session)
        self._bindings = AliasBindingService(session)
        self._assets = AssetService(session)
        self._acl = AccessPolicyService(session)

    async def _scope(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None,
    ) -> ContentActorScope:
        if scope is not None:
            return scope
        return await resolve_actor_scope(self._session, principal=principal, employee=employee)

    async def _target_for_version(
        self,
        *,
        asset: ContentAssetRow,
        ver: ContentBlobVersionRow,
        ttl_seconds: int,
    ) -> DownloadTarget:
        if not ver.sha256 and not (ver.object_metadata or {}).get("sealed"):
            raise AppError(
                code=ContentErrorCode.NOT_READY,
                title="Not Ready",
                status=422,
                detail="version bytes are not finalized",
            )
        store = ensure_file_store()
        url = await store.presign_get(ver.storage_key, ttl_seconds=ttl_seconds)
        filename = safe_filename(asset.title) or safe_filename(f"{asset.id}.bin")
        if url.startswith("file://"):
            return DownloadTarget(
                url=None,
                storage_key=ver.storage_key,
                content_type=asset.mime,
                filename=filename,
                sha256=ver.sha256,
                size=ver.size,
            )
        # storage_key stays set: proxy downloads (``?proxy=1``) and inline
        # reads fetch by key even on the S3 backend, while the presigned url
        # serves the default 302 flow.
        return DownloadTarget(
            url=url,
            storage_key=ver.storage_key,
            content_type=asset.mime,
            filename=filename,
            sha256=ver.sha256,
            size=ver.size,
        )

    async def resolve_alias_slug(
        self,
        *,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        ttl_seconds: int = 900,
        scope: ContentActorScope | None = None,
    ) -> DownloadTarget:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        alias = await self._aliases.get_by_slug_optional(slug)
        if alias is None or not alias_tenant_visible(alias, actor):
            raise not_found("alias not found")
        await self._acl.require_read_alias(
            principal=principal, employee=employee, alias=alias, scope=actor
        )
        binding = await self._bindings.get_current(alias.id)
        if binding is None:
            raise AppError(
                code=ContentErrorCode.NOT_READY,
                title="Not Ready",
                status=404,
                detail="alias has no bound asset",
            )
        asset = await self._session.get(ContentAssetRow, binding.asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
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
        scope: ContentActorScope | None = None,
    ) -> DownloadTarget:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        ver = await self._assets.resolve_blob_version(asset_id, blob_version_id=blob_version_id)
        return await self._target_for_version(asset=asset, ver=ver, ttl_seconds=ttl_seconds)

    async def read_asset_bytes(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        blob_version_id: str | None = None,
        scope: ContentActorScope | None = None,
    ) -> tuple[bytes, str]:
        target = await self.presign_asset(
            asset_id=asset_id,
            principal=principal,
            employee=employee,
            blob_version_id=blob_version_id,
            scope=scope,
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


__all__ = ["DownloadService", "DownloadTarget", "safe_filename"]
