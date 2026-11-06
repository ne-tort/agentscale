"""AccessPolicyService — ACL evaluation for content assets and aliases.

Two independent gates, in this order:

1. **Tenant boundary** (``scope.asset_tenant_visible`` / ``alias_tenant_visible``).
   A company-owned object outside the caller's tenants is reported as
   ``404 NOT_FOUND``; existence never leaks. Platform-owned objects are published
   and proceed to the ACL gate.
2. **ACL gate** — an explicit ``content_acl_entries`` grant, or the default
   access implied by ``visibility`` plus ownership. Same-tenant but ungranted
   objects are ``403 FORBIDDEN``.

Principal kinds matched by ``ContentActorScope.acl_principal_keys()``:
``employee``, ``company`` (each membership), ``cabinet`` / ``project`` (only when
the caller is attached *and* the id is the validated request header), ``role``
(JWT roles plus membership roles) and ``platform`` (platform admins only).

Every service entry point in this package calls one of the ``require_*`` methods
before touching bytes or rows; ``CONTRACT.md`` §4 lists the matrix.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.contracts import (
    ContentErrorCode,
    ContentPrincipalKind,
    acl_duplicate_keys,
    acl_grant_shallow_problem,
)
from prodavan.application.content.scope import (
    ContentActorScope,
    alias_tenant_visible,
    asset_tenant_visible,
    forbidden,
    not_found,
    resolve_actor_scope,
)
from prodavan.domain.content.types import (
    AclPermission,
    AliasStatus,
    ContentResourceKind,
    ContentVisibility,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.content import (
    ContentAclEntryRow,
    ContentAliasRow,
    ContentAssetRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class AccessPolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- scope resolution ---------------------------------------------------

    async def scope_for(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
    ) -> ContentActorScope:
        if scope is not None:
            return scope
        return await resolve_actor_scope(
            self._session,
            principal=principal,
            employee=employee,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )

    # -- low level ACL reads ------------------------------------------------

    async def _has_explicit_grant(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        scope: ContentActorScope,
        permission: AclPermission,
    ) -> bool:
        principals = scope.acl_principal_keys()
        if not principals:
            return False
        clauses = [
            (ContentAclEntryRow.principal_kind == pk) & (ContentAclEntryRow.principal_id == pid)
            for pk, pid in principals
        ]
        q = await self._session.execute(
            select(ContentAclEntryRow.id).where(
                ContentAclEntryRow.resource_kind == str(resource_kind),
                ContentAclEntryRow.resource_id == resource_id,
                ContentAclEntryRow.permission == str(permission),
                or_(*clauses),
            )
        )
        return q.first() is not None

    async def _default_company_access(
        self,
        *,
        owner_company_id: str | None,
        visibility: str,
        scope: ContentActorScope,
        created_by: str | None,
    ) -> bool:
        if not scope.is_employee and not scope.is_platform_admin:
            return False
        if visibility == ContentVisibility.PRIVATE:
            return (
                scope.employee_id is not None
                and created_by is not None
                and scope.employee_id == created_by
            )
        if visibility == ContentVisibility.COMPANY and owner_company_id:
            return owner_company_id in scope.tenant_ids
        return False

    # -- aliases ------------------------------------------------------------

    @staticmethod
    def _default_alias_read(alias: ContentAliasRow, scope: ContentActorScope) -> bool:
        """Tenant default for reading an alias.

        ``content_aliases`` has no ``created_by`` column, so a private alias
        cannot be scoped to its creator the way a private asset is. Creation of
        ``visibility=private`` is therefore refused
        (``CONTENT_ALIAS_VISIBILITY_UNSUPPORTED``) and reads follow tenancy;
        legacy private rows keep working for their own tenant instead of becoming
        unreadable for everyone.
        """
        return bool(alias.owner_company_id) and alias.owner_company_id in scope.tenant_ids

    @staticmethod
    def _default_alias_write(alias: ContentAliasRow, scope: ContentActorScope) -> bool:
        """Managing a permalink follows tenancy, not ``visibility``.

        ``visibility`` hides the bound bytes; it must not lock the owning tenant
        out of disabling, re-enabling or re-binding its own permalink.
        """
        return bool(alias.owner_company_id) and alias.owner_company_id in scope.tenant_ids

    async def can_read_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
        scope: ContentActorScope | None = None,
        allow_disabled: bool = False,
    ) -> bool:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not alias_tenant_visible(alias, actor):
            return False
        if not allow_disabled and alias.status != AliasStatus.ACTIVE:
            return False
        if actor.is_platform_admin:
            return True
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ALIAS,
            resource_id=alias.id,
            scope=actor,
            permission=AclPermission.READ,
        ):
            return True
        return self._default_alias_read(alias, actor)

    async def require_read_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
        scope: ContentActorScope | None = None,
        allow_disabled: bool = False,
    ) -> None:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not alias_tenant_visible(alias, actor):
            raise not_found("alias not found")
        if not await self.can_read_alias(
            principal=principal,
            employee=employee,
            alias=alias,
            scope=actor,
            allow_disabled=allow_disabled,
        ):
            raise forbidden("no read access to alias")

    async def can_write_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
        scope: ContentActorScope | None = None,
    ) -> bool:
        """Write/manage access; independent of ``status`` so a disabled alias
        can still be re-enabled or unbound by its owners."""
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not alias_tenant_visible(alias, actor):
            return False
        if actor.is_platform_admin:
            return True
        for permission in (AclPermission.WRITE, AclPermission.ADMIN):
            if await self._has_explicit_grant(
                resource_kind=ContentResourceKind.ALIAS,
                resource_id=alias.id,
                scope=actor,
                permission=permission,
            ):
                return True
        return self._default_alias_write(alias, actor)

    async def require_write_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
        scope: ContentActorScope | None = None,
    ) -> None:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not alias_tenant_visible(alias, actor):
            raise not_found("alias not found")
        if not await self.can_write_alias(
            principal=principal, employee=employee, alias=alias, scope=actor
        ):
            raise forbidden("no write access to alias")

    async def require_admin_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
        scope: ContentActorScope | None = None,
    ) -> None:
        """Rewriting an alias ACL needs ownership or an explicit ``admin`` grant.

        A bare ``write`` grant must not be able to re-grant access: that would let
        a writer escalate itself to ``admin`` and mint grants for other people.
        """
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not alias_tenant_visible(alias, actor):
            raise not_found("alias not found")
        if actor.is_platform_admin:
            return
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ALIAS,
            resource_id=alias.id,
            scope=actor,
            permission=AclPermission.ADMIN,
        ):
            return
        if self._default_alias_write(alias, actor):
            # Tenant ownership is the alias equivalent of "the owning company
            # administers its own permalink"; there is no created_by column.
            return
        raise forbidden("admin access to alias required")

    # -- assets -------------------------------------------------------------

    async def can_read_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
        scope: ContentActorScope | None = None,
    ) -> bool:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not asset_tenant_visible(asset, actor):
            return False
        if actor.is_platform_admin:
            return True
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ASSET,
            resource_id=asset.id,
            scope=actor,
            permission=AclPermission.READ,
        ):
            return True
        return await self._default_company_access(
            owner_company_id=asset.owner_company_id,
            visibility=asset.visibility,
            scope=actor,
            created_by=asset.created_by,
        )

    async def require_read_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
        scope: ContentActorScope | None = None,
    ) -> None:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        if not await self.can_read_asset(
            principal=principal, employee=employee, asset=asset, scope=actor
        ):
            raise forbidden("no read access to asset")

    async def require_write_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
        scope: ContentActorScope | None = None,
    ) -> None:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        if await self.can_write_asset(scope=actor, asset=asset):
            return
        raise forbidden("no write access to asset")

    async def can_write_asset(
        self,
        *,
        scope: ContentActorScope,
        asset: ContentAssetRow,
    ) -> bool:
        if scope.is_platform_admin:
            return True
        for permission in (AclPermission.WRITE, AclPermission.ADMIN):
            if await self._has_explicit_grant(
                resource_kind=ContentResourceKind.ASSET,
                resource_id=asset.id,
                scope=scope,
                permission=permission,
            ):
                return True
        if (
            asset.visibility == ContentVisibility.PRIVATE
            and scope.employee_id
            and asset.created_by == scope.employee_id
        ):
            return True
        if (
            asset.visibility == ContentVisibility.COMPANY
            and asset.owner_company_id
            and asset.owner_company_id in scope.tenant_ids
        ):
            return True
        return False

    async def require_admin_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
        scope: ContentActorScope | None = None,
    ) -> None:
        """ACL rewrite / archive needs an explicit ``admin`` grant or ownership."""
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if not asset_tenant_visible(asset, actor):
            raise not_found("asset not found")
        if actor.is_platform_admin:
            return
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ASSET,
            resource_id=asset.id,
            scope=actor,
            permission=AclPermission.ADMIN,
        ):
            return
        if (
            asset.visibility == ContentVisibility.PRIVATE
            and actor.employee_id
            and asset.created_by == actor.employee_id
        ):
            return
        if (
            asset.visibility == ContentVisibility.COMPANY
            and asset.owner_company_id
            and asset.owner_company_id in actor.tenant_ids
        ):
            return
        raise forbidden("admin access to asset required")

    # -- ACL administration ---------------------------------------------------

    async def _resource_tenant_id(
        self, *, resource_kind: ContentResourceKind, resource_id: str
    ) -> str | None:
        if resource_kind == ContentResourceKind.ASSET:
            asset = await self._session.get(ContentAssetRow, resource_id)
            return asset.owner_company_id if asset is not None else None
        alias = await self._session.get(ContentAliasRow, resource_id)
        return alias.owner_company_id if alias is not None else None

    async def require_admin_acl(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> None:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if resource_kind == ContentResourceKind.ASSET:
            asset = await self._session.get(ContentAssetRow, resource_id)
            if asset is None:
                raise not_found("asset not found")
            await self.require_admin_asset(
                principal=principal, employee=employee, asset=asset, scope=actor
            )
            return
        alias = await self._session.get(ContentAliasRow, resource_id)
        if alias is None:
            raise not_found("alias not found")
        await self.require_admin_alias(
            principal=principal, employee=employee, alias=alias, scope=actor
        )

    async def list_acl(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> list[dict]:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        if resource_kind == ContentResourceKind.ASSET:
            asset = await self._session.get(ContentAssetRow, resource_id)
            if asset is None:
                raise not_found("asset not found")
            await self.require_read_asset(
                principal=principal, employee=employee, asset=asset, scope=actor
            )
        else:
            alias = await self._session.get(ContentAliasRow, resource_id)
            if alias is None:
                raise not_found("alias not found")
            # The ACL of a disabled alias stays inspectable: it is grant
            # metadata, not the permalink's bytes.
            await self.require_read_alias(
                principal=principal,
                employee=employee,
                alias=alias,
                scope=actor,
                allow_disabled=True,
            )
        return await self._read_entries(
            resource_kind=resource_kind, resource_id=resource_id
        )

    async def _read_entries(
        self, *, resource_kind: ContentResourceKind, resource_id: str
    ) -> list[dict]:
        q = await self._session.execute(
            select(ContentAclEntryRow)
            .where(
                ContentAclEntryRow.resource_kind == str(resource_kind),
                ContentAclEntryRow.resource_id == resource_id,
            )
            .order_by(ContentAclEntryRow.created_at, ContentAclEntryRow.id)
        )
        return [
            {
                "id": r.id,
                "principal_kind": r.principal_kind,
                "principal_id": r.principal_id,
                "permission": r.permission,
            }
            for r in q.scalars().all()
        ]

    async def replace_acl(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        entries: list[dict],
        principal: Principal,
        employee: EmployeeRow | None,
        scope: ContentActorScope | None = None,
    ) -> list[dict]:
        actor = await self.scope_for(principal=principal, employee=employee, scope=scope)
        await self.require_admin_acl(
            resource_kind=resource_kind,
            resource_id=resource_id,
            principal=principal,
            employee=employee,
            scope=actor,
        )
        resource_tenant_id = await self._resource_tenant_id(
            resource_kind=resource_kind, resource_id=resource_id
        )
        normalized = await self.validate_acl_entries(
            entries=entries,
            actor=actor,
            resource_tenant_id=resource_tenant_id,
        )
        q = await self._session.execute(
            select(ContentAclEntryRow).where(
                ContentAclEntryRow.resource_kind == str(resource_kind),
                ContentAclEntryRow.resource_id == resource_id,
            )
        )
        for row in q.scalars().all():
            await self._session.delete(row)
        for entry in normalized:
            self._session.add(
                ContentAclEntryRow(
                    resource_kind=str(resource_kind),
                    resource_id=resource_id,
                    principal_kind=entry["principal_kind"],
                    principal_id=entry["principal_id"],
                    permission=entry["permission"],
                )
            )
        await self._session.commit()
        return await self._read_entries(resource_kind=resource_kind, resource_id=resource_id)

    # -- ACL entry validation -------------------------------------------------

    async def validate_acl_entries(
        self,
        *,
        entries: Iterable[Mapping[str, object]],
        actor: ContentActorScope,
        resource_tenant_id: str | None,
    ) -> list[dict]:
        """Validate and normalise a replacement ACL payload.

        Rejects unsupported kinds/permissions, duplicates, cross-tenant grantees
        and (for non platform admins) any ``platform`` grant. Row-scoped
        existence checks confirm the grantee belongs to the resource's tenant.
        """
        normalized: list[dict] = []
        for raw in entries:
            kind = str(raw.get("principal_kind", "")).strip()
            principal_id = str(raw.get("principal_id", "")).strip()
            permission = str(raw.get("permission") or AclPermission.READ).strip()

            problem = acl_grant_shallow_problem(
                principal_kind=kind,
                principal_id=principal_id,
                permission=permission,
                resource_tenant_id=resource_tenant_id,
                actor_is_platform_admin=actor.is_platform_admin,
            )
            if problem is not None:
                raise AppError(
                    code=problem,
                    title="Invalid access entry",
                    status=422,
                    detail=f"rejected ACL entry: {kind}:{permission}",
                )
            await self._require_grantee_in_tenant(
                kind=kind,
                principal_id=principal_id,
                resource_tenant_id=resource_tenant_id,
                actor=actor,
            )
            normalized.append(
                {"principal_kind": kind, "principal_id": principal_id, "permission": permission}
            )

        duplicates = acl_duplicate_keys(normalized)
        if duplicates:
            raise AppError(
                code=ContentErrorCode.CONTENT_ACL_DUPLICATE,
                title="Duplicate access entry",
                status=422,
                detail="the same principal and permission appear more than once",
            )
        return normalized

    async def _require_grantee_in_tenant(
        self,
        *,
        kind: str,
        principal_id: str,
        resource_tenant_id: str | None,
        actor: ContentActorScope,
    ) -> None:
        if actor.is_platform_admin:
            return
        if kind == ContentPrincipalKind.EMPLOYEE:
            from prodavan.application.relations.query import RelationsQuery

            if not resource_tenant_id:
                raise AppError(
                    code=ContentErrorCode.CONTENT_ACL_CROSS_TENANT,
                    title="Invalid access entry",
                    status=422,
                    detail="employee grants require a tenant-owned resource",
                )
            relations = RelationsQuery(self._session)
            if not await relations.has_membership(
                employee_id=principal_id, company_id=resource_tenant_id
            ):
                raise AppError(
                    code=ContentErrorCode.CONTENT_ACL_CROSS_TENANT,
                    title="Invalid access entry",
                    status=422,
                    detail="grantee is not a member of the resource tenant",
                )
        elif kind == ContentPrincipalKind.CABINET:
            cabinet = await self._session.get(CabinetInstanceRow, principal_id)
            if cabinet is None or (
                resource_tenant_id and cabinet.company_id != resource_tenant_id
            ):
                raise AppError(
                    code=ContentErrorCode.CONTENT_ACL_CROSS_TENANT,
                    title="Invalid access entry",
                    status=422,
                    detail="cabinet grant must belong to the resource tenant",
                )
        elif kind == ContentPrincipalKind.PROJECT:
            project = await self._session.get(ProjectRow, principal_id)
            if project is None or (
                resource_tenant_id and project.company_id != resource_tenant_id
            ):
                raise AppError(
                    code=ContentErrorCode.CONTENT_ACL_CROSS_TENANT,
                    title="Invalid access entry",
                    status=422,
                    detail="project grant must belong to the resource tenant",
                )
        elif kind == ContentPrincipalKind.ROLE:
            if principal_id.startswith("platform.") and not actor.is_platform_admin:
                raise AppError(
                    code=ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN,
                    title="Invalid access entry",
                    status=422,
                    detail="platform roles may only be granted by platform admins",
                )

    # -- module ownership helper ---------------------------------------------

    async def module_tenant_id(self, module_id: str) -> str | None:
        module = await self._session.get(ModuleRow, module_id)
        return module.owner_company_id if module is not None else None


__all__ = ["AccessPolicyService"]
