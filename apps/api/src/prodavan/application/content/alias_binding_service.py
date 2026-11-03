"""AliasBindingService — temporal alias ↔ asset version links.

A binding records *which* immutable blob version an alias pointed at and when it
was superseded, so resolve history is evidence and is never rewritten: binding a
new target marks the previous row ``superseded_at`` and inserts a new row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.contracts import ContentErrorCode
from prodavan.application.content.scope import (
    ContentActorScope,
    asset_tenant_visible,
    alias_tenant_visible,
    forbidden,
    not_found,
    resolve_actor_scope,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.content import (
    ContentAliasBindingRow,
    ContentAliasRow,
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def binding_public(row: ContentAliasBindingRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "alias_id": row.alias_id,
        "asset_id": row.asset_id,
        "blob_version_id": row.blob_version_id,
        "effective_at": row.effective_at.isoformat() if row.effective_at else None,
        "superseded_at": row.superseded_at.isoformat() if row.superseded_at else None,
        "bound_by": row.bound_by,
    }


class AliasBindingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
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

    async def get_current(self, alias_id: str) -> ContentAliasBindingRow | None:
        q = await self._session.execute(
            select(ContentAliasBindingRow)
            .where(
                ContentAliasBindingRow.alias_id == alias_id,
                ContentAliasBindingRow.superseded_at.is_(None),
            )
            .order_by(ContentAliasBindingRow.effective_at.desc())
            .limit(1)
        )
        return q.scalars().first()

    async def _require_alias(
        self,
        *,
        alias_id: str,
        actor: ContentActorScope,
    ) -> ContentAliasRow:
        alias = await self._session.get(ContentAliasRow, alias_id)
        if alias is None or not alias_tenant_visible(alias, actor):
            raise not_found("alias not found")
        return alias

    async def bind(
        self,
        *,
        alias_id: str,
        asset_id: str,
        blob_version_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        alias = await self._require_alias(alias_id=alias_id, actor=actor)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_write_alias(
            principal=principal, employee=employee, alias=alias, scope=actor
        )
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        # Cross-tenant binding would publish one tenant's bytes through another
        # tenant's permalink.
        if (
            alias.owner_company_id
            and asset.owner_company_id
            and alias.owner_company_id != asset.owner_company_id
            and not actor.is_platform_admin
        ):
            raise forbidden("alias and asset belong to different companies")

        version: ContentBlobVersionRow | None = None
        if blob_version_id:
            version = await self._session.get(ContentBlobVersionRow, blob_version_id)
            if version is None or version.asset_id != asset_id:
                raise not_found("version not found")
            if not self._is_sealed(version):
                raise AppError(
                    code=ContentErrorCode.NOT_READY,
                    title="Not Ready",
                    status=422,
                    detail="version bytes are not sealed; finalize it before binding",
                )
        else:
            version = await self._latest_sealed_version(asset_id)
            if version is None:
                raise AppError(
                    code=ContentErrorCode.NOT_READY,
                    title="Not Ready",
                    status=422,
                    detail="asset has no sealed version to bind",
                )

        now = datetime.now(UTC)
        current = await self.get_current(alias_id)
        if (
            current is not None
            and current.asset_id == asset_id
            and current.blob_version_id == version.id
        ):
            return {**binding_public(current), "replayed": True}
        if current is not None:
            current.superseded_at = now
        row = ContentAliasBindingRow(
            alias_id=alias_id,
            asset_id=asset_id,
            blob_version_id=version.id,
            bound_by=actor.employee_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return binding_public(row)

    async def unbind(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        alias = await self._require_alias(alias_id=alias_id, actor=actor)
        await self._acl.require_write_alias(
            principal=principal, employee=employee, alias=alias, scope=actor
        )
        current = await self.get_current(alias_id)
        if current is None:
            return {"unbound": False, "alias_id": alias_id}
        current.superseded_at = datetime.now(UTC)
        await self._session.commit()
        return {"unbound": True, "alias_id": alias_id, "binding_id": current.id}

    async def history(
        self,
        alias_id: str,
        *,
        limit: int = 20,
        scope: ContentActorScope | None = None,
    ) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ContentAliasBindingRow)
            .where(ContentAliasBindingRow.alias_id == alias_id)
            .order_by(ContentAliasBindingRow.effective_at.desc(), ContentAliasBindingRow.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return [binding_public(r) for r in q.scalars().all()]

    async def _latest_sealed_version(self, asset_id: str) -> ContentBlobVersionRow | None:
        q = await self._session.execute(
            select(ContentBlobVersionRow)
            .where(
                ContentBlobVersionRow.asset_id == asset_id,
                ContentBlobVersionRow.sha256.is_not(None),
            )
            .order_by(ContentBlobVersionRow.version.desc())
            .limit(1)
        )
        return q.scalars().first()

    @staticmethod
    def _is_sealed(version: ContentBlobVersionRow) -> bool:
        return bool(version.sha256) or bool((version.object_metadata or {}).get("sealed"))


__all__ = ["AliasBindingService", "binding_public"]
