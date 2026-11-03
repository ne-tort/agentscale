"""AssetLinkService — asset ↔ domain entity edges (``content_asset_links``).

The table is 1:1 per ``(link_kind, link_id)`` (``uq_content_asset_link_target``):
one asset owns a given project attachment / project / module slot. Re-pointing a
slot is an explicit, audited replacement, never an implicit upsert from the
public API — a silent replacement would steal another asset's edge.

Caller-visible kinds are ``PUBLIC_LINK_KINDS``. ``documents`` is written only by
the documents domain service, so a caller cannot claim a document slot.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.contracts import (
    INTERNAL_LINK_KINDS,
    MAX_LINK_ID_LEN,
    MAX_LINK_KIND_LEN,
    ContentErrorCode,
    ContentLinkKind,
    link_kind_is_caller_assignable,
    normalize_link_kind,
)
from prodavan.application.content.scope import (
    ContentActorScope,
    asset_tenant_visible,
    forbidden,
    not_found,
    resolve_actor_scope,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.content import (
    ContentAssetLinkRow,
    ContentAssetRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleRow
from prodavan.infrastructure.persistence.models.projects import ProjectAttachmentRow, ProjectRow


def link_public(row: ContentAssetLinkRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "asset_id": row.asset_id,
        "link_kind": row.link_kind,
        "link_id": row.link_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class AssetLinkService:
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

    async def list_for_asset(
        self,
        *,
        asset_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> list[dict[str, Any]]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        q = await self._session.execute(
            select(ContentAssetLinkRow)
            .where(ContentAssetLinkRow.asset_id == asset_id)
            .order_by(ContentAssetLinkRow.created_at, ContentAssetLinkRow.id)
        )
        return [link_public(row) for row in q.scalars().all()]

    async def link(
        self,
        *,
        asset_id: str,
        link_kind: str,
        link_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        replace: bool = False,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        kind = normalize_link_kind(link_kind)
        if len(kind) > MAX_LINK_KIND_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail="link_kind too long",
            )
        target_id = (link_id or "").strip()
        if not target_id or len(target_id) > MAX_LINK_ID_LEN:
            raise AppError(
                code=ContentErrorCode.VALIDATION_ERROR,
                title="Validation Error",
                status=422,
                detail=f"link_id must contain 1..{MAX_LINK_ID_LEN} characters",
            )
        if kind in INTERNAL_LINK_KINDS:
            raise AppError(
                code=ContentErrorCode.CONTENT_LINK_KIND_UNSUPPORTED,
                title="Unsupported link kind",
                status=422,
                detail="this link kind is owned by its domain service",
            )
        if not link_kind_is_caller_assignable(kind):
            raise AppError(
                code=ContentErrorCode.CONTENT_LINK_KIND_UNSUPPORTED,
                title="Unsupported link kind",
                status=422,
                detail="unknown link_kind",
            )

        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        await self._require_target_in_tenant(
            kind=kind, link_id=target_id, asset=asset, actor=actor
        )

        q = await self._session.execute(
            select(ContentAssetLinkRow).where(
                ContentAssetLinkRow.link_kind == kind,
                ContentAssetLinkRow.link_id == target_id,
            )
        )
        existing = q.scalars().first()
        if existing is not None:
            if existing.asset_id == asset_id:
                return {**link_public(existing), "replayed": True}
            if not replace:
                raise AppError(
                    code=ContentErrorCode.CONTENT_LINK_TARGET_CONFLICT,
                    title="Link target already bound",
                    status=409,
                    detail="another asset already owns this link target",
                )
            previous_asset = await self._session.get(ContentAssetRow, existing.asset_id)
            if previous_asset is not None:
                await self._acl.require_admin_asset(
                    principal=principal,
                    employee=employee,
                    asset=previous_asset,
                    scope=actor,
                )
            existing.asset_id = asset_id
            await self._session.commit()
            await self._session.refresh(existing)
            return link_public(existing)

        row = ContentAssetLinkRow(asset_id=asset_id, link_kind=kind, link_id=target_id)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return link_public(row)

    async def unlink(
        self,
        *,
        asset_id: str,
        link_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> dict[str, Any]:
        actor = await self._scope(principal=principal, employee=employee, scope=scope)
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None or not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        await self._acl.require_write_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        )
        row = await self._session.get(ContentAssetLinkRow, link_id)
        if row is None or row.asset_id != asset_id:
            raise not_found("asset link not found")
        if row.link_kind in INTERNAL_LINK_KINDS:
            raise forbidden("this link kind is owned by its domain service")
        public = link_public(row)
        await self._session.delete(row)
        await self._session.commit()
        return {"unlinked": True, **public}

    async def _require_target_in_tenant(
        self,
        *,
        kind: str,
        link_id: str,
        asset: ContentAssetRow,
        actor: ContentActorScope,
    ) -> None:
        """The target row must exist and belong to the asset's tenant."""
        target_tenant: str | None
        if kind == ContentLinkKind.PROJECT.value:
            project = await self._session.get(ProjectRow, link_id)
            if project is None or project.status == "deleted":
                raise not_found("project not found")
            target_tenant = project.company_id
        elif kind == ContentLinkKind.PROJECT_ATTACHMENT.value:
            attachment = await self._session.get(ProjectAttachmentRow, link_id)
            if attachment is None:
                raise not_found("attachment not found")
            project = await self._session.get(ProjectRow, attachment.project_id)
            if project is None:
                raise not_found("attachment not found")
            target_tenant = project.company_id
        elif kind == ContentLinkKind.MODULE.value:
            module = await self._session.get(ModuleRow, link_id)
            if module is None:
                raise not_found("module not found")
            target_tenant = module.owner_company_id
        elif kind == ContentLinkKind.MODULE_CATALOG.value:
            module = await self._session.get(ModuleRow, link_id)
            if module is None:
                raise not_found("module not found")
            target_tenant = module.owner_company_id
        else:
            target_tenant = None

        if actor.is_platform_admin:
            return
        if asset.owner_company_id is None:
            # Platform published assets may link into any tenant the caller owns.
            if target_tenant is not None and target_tenant not in actor.tenant_ids:
                raise not_found("link target not found")
            return
        if target_tenant != asset.owner_company_id:
            raise not_found("link target not found")


__all__ = ["AssetLinkService", "link_public"]
