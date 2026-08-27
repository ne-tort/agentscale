"""AccessPolicyService — separate alias vs asset ACL checks."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.content.types import (
    AclPermission,
    AclPrincipalKind,
    ContentResourceKind,
    ContentVisibility,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.content import (
    ContentAclEntryRow,
    ContentAliasRow,
    ContentAssetRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow, MembershipRow


class AccessPolicyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _employee_company_ids(self, employee_id: str) -> set[str]:
        q = await self._session.execute(
            select(MembershipRow.company_id).where(MembershipRow.employee_id == employee_id)
        )
        return set(q.scalars().all())

    async def _principal_keys(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[tuple[str, str]]:
        principals: list[tuple[str, str]] = []
        if employee is not None:
            principals.append((AclPrincipalKind.EMPLOYEE, employee.id))
            for cid in await self._employee_company_ids(employee.id):
                principals.append((AclPrincipalKind.COMPANY, cid))
        if principal.is_platform_admin:
            principals.append((AclPrincipalKind.PLATFORM, "platform"))
        return principals

    async def _has_explicit_grant(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        permission: AclPermission,
    ) -> bool:
        principals = await self._principal_keys(principal=principal, employee=employee)
        if not principals:
            return False
        clauses = [
            (ContentAclEntryRow.principal_kind == pk) & (ContentAclEntryRow.principal_id == pid)
            for pk, pid in principals
        ]
        q = await self._session.execute(
            select(ContentAclEntryRow.id).where(
                ContentAclEntryRow.resource_kind == resource_kind,
                ContentAclEntryRow.resource_id == resource_id,
                ContentAclEntryRow.permission == permission,
                or_(*clauses),
            )
        )
        return q.first() is not None

    async def _default_company_access(
        self,
        *,
        owner_company_id: str | None,
        visibility: str,
        employee: EmployeeRow | None,
        created_by: str | None,
    ) -> bool:
        if employee is None:
            return False
        if visibility == ContentVisibility.PRIVATE:
            return created_by is not None and employee.id == created_by
        if visibility == ContentVisibility.COMPANY and owner_company_id:
            return owner_company_id in await self._employee_company_ids(employee.id)
        return False

    async def can_read_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
    ) -> bool:
        if alias.status != "active":
            return False
        if principal.is_platform_admin:
            return True
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ALIAS,
            resource_id=alias.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.READ,
        ):
            return True
        return await self._default_company_access(
            owner_company_id=alias.owner_company_id,
            visibility=alias.visibility,
            employee=employee,
            created_by=None,
        )

    async def require_read_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
    ) -> None:
        if not await self.can_read_alias(principal=principal, employee=employee, alias=alias):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="no read access to alias",
            )

    async def can_write_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
    ) -> bool:
        if principal.is_platform_admin:
            return True
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ALIAS,
            resource_id=alias.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.WRITE,
        ) or await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ALIAS,
            resource_id=alias.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.ADMIN,
        ):
            return True
        return await self._default_company_access(
            owner_company_id=alias.owner_company_id,
            visibility=alias.visibility,
            employee=employee,
            created_by=None,
        )

    async def require_write_alias(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        alias: ContentAliasRow,
    ) -> None:
        if not await self.can_write_alias(principal=principal, employee=employee, alias=alias):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="no write access to alias",
            )

    async def can_read_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
    ) -> bool:
        if principal.is_platform_admin:
            return True
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ASSET,
            resource_id=asset.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.READ,
        ):
            return True
        return await self._default_company_access(
            owner_company_id=asset.owner_company_id,
            visibility=asset.visibility,
            employee=employee,
            created_by=asset.created_by,
        )

    async def require_read_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
    ) -> None:
        if not await self.can_read_asset(principal=principal, employee=employee, asset=asset):
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="no read access to asset",
            )

    async def require_write_asset(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        asset: ContentAssetRow,
    ) -> None:
        if principal.is_platform_admin:
            return
        if await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ASSET,
            resource_id=asset.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.WRITE,
        ) or await self._has_explicit_grant(
            resource_kind=ContentResourceKind.ASSET,
            resource_id=asset.id,
            principal=principal,
            employee=employee,
            permission=AclPermission.ADMIN,
        ):
            return
        if asset.visibility == ContentVisibility.PRIVATE and employee and asset.created_by == employee.id:
            return
        if (
            asset.visibility == ContentVisibility.COMPANY
            and asset.owner_company_id
            and employee
            and asset.owner_company_id in await self._employee_company_ids(employee.id)
        ):
            return
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="no write access to asset",
        )

    async def require_admin_acl(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        if resource_kind == ContentResourceKind.ASSET:
            asset = await self._session.get(ContentAssetRow, resource_id)
            if asset is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
            await self.require_write_asset(principal=principal, employee=employee, asset=asset)
            return
        alias = await self._session.get(ContentAliasRow, resource_id)
        if alias is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        await self.require_write_alias(principal=principal, employee=employee, alias=alias)

    async def list_acl(
        self,
        *,
        resource_kind: ContentResourceKind,
        resource_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        if resource_kind == ContentResourceKind.ASSET:
            asset = await self._session.get(ContentAssetRow, resource_id)
            if asset is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
            await self.require_read_asset(principal=principal, employee=employee, asset=asset)
        else:
            alias = await self._session.get(ContentAliasRow, resource_id)
            if alias is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
            await self.require_read_alias(principal=principal, employee=employee, alias=alias)
        q = await self._session.execute(
            select(ContentAclEntryRow)
            .where(
                ContentAclEntryRow.resource_kind == resource_kind,
                ContentAclEntryRow.resource_id == resource_id,
            )
            .order_by(ContentAclEntryRow.created_at)
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
    ) -> list[dict]:
        await self.require_admin_acl(
            resource_kind=resource_kind,
            resource_id=resource_id,
            principal=principal,
            employee=employee,
        )
        q = await self._session.execute(
            select(ContentAclEntryRow).where(
                ContentAclEntryRow.resource_kind == resource_kind,
                ContentAclEntryRow.resource_id == resource_id,
            )
        )
        for row in q.scalars().all():
            await self._session.delete(row)
        for entry in entries:
            row = ContentAclEntryRow(
                resource_kind=resource_kind,
                resource_id=resource_id,
                principal_kind=str(entry["principal_kind"]),
                principal_id=str(entry["principal_id"]),
                permission=str(entry.get("permission") or AclPermission.READ),
            )
            self._session.add(row)
        await self._session.commit()
        return await self.list_acl(
            resource_kind=resource_kind,
            resource_id=resource_id,
            principal=principal,
            employee=employee,
        )
