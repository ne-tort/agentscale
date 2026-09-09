"""Tenant Infra Objects — Content BC scoped to project (no MinIO DSN to Pod)."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_OBJECTS, PodBridgeClaims
from prodavan.application.tenant_infra.publish import emit_tenant_infra_event
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService, enforce_ops_rate, quota_exceeded
from prodavan.domain.content.types import ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.files.keys import new_blob_key
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.content import ContentAssetRow, ContentBlobVersionRow

TAG_PLANE = "tenant_infra"
TAG_PROJECT_PREFIX = "project:"


def _project_tag(project_id: str) -> str:
    return f"{TAG_PROJECT_PREFIX}{project_id}"


class TenantObjectsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_OBJECTS)

    async def _quota(self, bridge: PodBridgeClaims):
        return await TenantInfraQuotaService(self._session).get_quota(bridge.company_id)

    async def _project_assets(self, *, company_id: str, project_id: str) -> list[ContentAssetRow]:
        result = await self._session.execute(
            select(ContentAssetRow).where(ContentAssetRow.owner_company_id == company_id)
        )
        tag = _project_tag(project_id)
        out: list[ContentAssetRow] = []
        for row in result.scalars().all():
            tags = row.tags or []
            if isinstance(tags, list) and TAG_PLANE in tags and tag in tags:
                out.append(row)
        return out

    async def put(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        name: str,
        content: bytes,
        mime: str | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge)
        await enforce_ops_rate(
            plane="objects",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.objects_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        if len(content) > quota.objects_max_bytes:
            raise quota_exceeded(f"object exceeds {quota.objects_max_bytes} bytes")
        existing = await self._project_assets(company_id=bridge.company_id, project_id=bridge.project_id)
        if len(existing) >= quota.objects_max_per_project:
            raise quota_exceeded(f"objects max {quota.objects_max_per_project} exceeded")
        title = (name or "object").strip()[:260] or "object"
        asset = ContentAssetRow(
            owner_scope=OwnerScope.COMPANY,
            owner_company_id=bridge.company_id,
            visibility=ContentVisibility.COMPANY,
            mime=mime,
            title=title,
            tags=[TAG_PLANE, _project_tag(bridge.project_id), f"pod:{bridge.pod_id}"],
            created_by=None,
        )
        self._session.add(asset)
        await self._session.flush()
        store = ensure_file_store()
        storage_key = new_blob_key()
        await store.put_bytes(storage_key, content, content_type=mime)
        sha = hashlib.sha256(content).hexdigest()
        version = ContentBlobVersionRow(
            asset_id=asset.id,
            version=1,
            storage_key=storage_key,
            size=len(content),
            sha256=sha,
            etag=sha[:32],
            object_metadata={"source": "tenant_infra"},
        )
        self._session.add(version)
        await self._session.commit()
        await emit_tenant_infra_event(
            session=self._session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "objects.put", "asset_id": asset.id, "size": len(content)},
        )
        return {"id": asset.id, "title": asset.title, "size": len(content), "sha256": sha}

    async def list(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge)
        await enforce_ops_rate(
            plane="objects",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.objects_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        assets = await self._project_assets(company_id=bridge.company_id, project_id=bridge.project_id)
        items = [
            {
                "id": a.id,
                "title": a.title,
                "mime": a.mime,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in assets
        ]
        return {"items": items, "count": len(items)}

    async def get_bytes(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        asset_id: str,
    ) -> tuple[bytes, str | None, str | None]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge)
        await enforce_ops_rate(
            plane="objects",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.objects_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or asset.owner_company_id != bridge.company_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="object not found")
        tags = asset.tags or []
        if TAG_PLANE not in tags or _project_tag(bridge.project_id) not in tags:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="object project mismatch")
        result = await self._session.execute(
            select(ContentBlobVersionRow)
            .where(ContentBlobVersionRow.asset_id == asset.id)
            .order_by(ContentBlobVersionRow.version.desc())
            .limit(1)
        )
        ver = result.scalar_one_or_none()
        if ver is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="blob missing")
        store = ensure_file_store()
        data = await store.get_bytes(ver.storage_key)
        return data, asset.mime, asset.title

    async def delete(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        asset_id: str,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge)
        await enforce_ops_rate(
            plane="objects",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.objects_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or asset.owner_company_id != bridge.company_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="object not found")
        tags = asset.tags or []
        if TAG_PLANE not in tags or _project_tag(bridge.project_id) not in tags:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="object project mismatch")
        result = await self._session.execute(
            select(ContentBlobVersionRow).where(ContentBlobVersionRow.asset_id == asset.id)
        )
        store = ensure_file_store()
        for ver in result.scalars().all():
            try:
                await store.delete(ver.storage_key)
            except Exception:
                pass
            await self._session.delete(ver)
        await self._session.delete(asset)
        await self._session.commit()
        return {"id": asset_id, "ok": True}

    async def purge_project(self, *, company_id: str, project_id: str) -> int:
        assets = await self._project_assets(company_id=company_id, project_id=project_id)
        store = ensure_file_store()
        n = 0
        for asset in assets:
            result = await self._session.execute(
                select(ContentBlobVersionRow).where(ContentBlobVersionRow.asset_id == asset.id)
            )
            for ver in result.scalars().all():
                try:
                    await store.delete(ver.storage_key)
                except Exception:
                    pass
                await self._session.delete(ver)
            await self._session.delete(asset)
            n += 1
        if n:
            await self._session.commit()
        return n
