"""AliasService — permalink CRUD (no FileService)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.domain.content.types import AliasStatus, ContentVisibility
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.ownership import OwnerScope
from prodavan.infrastructure.persistence.models.content import ContentAliasRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,126}[a-z0-9]$")
_ALIAS_ID_RE = re.compile(r"^cal_[0-9a-f]{16}$")


def alias_public(row: ContentAliasRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.slug,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "visibility": row.visibility,
        "label": row.label,
        "description": row.description,
        "metadata": row.metadata_ or {},
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class AliasService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._acl = AccessPolicyService(session)

    @staticmethod
    def is_alias_id(value: str) -> bool:
        return bool(_ALIAS_ID_RE.match(value))

    @staticmethod
    def normalize_slug(slug: str) -> str:
        normalized = slug.strip().lower()
        if not _SLUG_RE.match(normalized):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="slug must be lowercase alphanumeric with . _ -",
            )
        return normalized

    async def create(
        self,
        *,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        owner_company_id: str | None,
        visibility: str = ContentVisibility.COMPANY,
        label: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        if employee is None and not principal.is_platform_admin:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        normalized = self.normalize_slug(slug)
        existing = await self.get_by_slug_optional(normalized)
        if existing is not None:
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail="slug already exists",
            )
        scope = OwnerScope.COMPANY if owner_company_id else OwnerScope.PLATFORM
        row = ContentAliasRow(
            slug=normalized,
            owner_scope=scope,
            owner_company_id=owner_company_id,
            visibility=visibility,
            label=label,
            description=description,
            metadata_=metadata or {},
            status=AliasStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return alias_public(row)

    async def get_by_slug_optional(self, slug: str) -> ContentAliasRow | None:
        q = await self._session.execute(
            select(ContentAliasRow).where(ContentAliasRow.slug == slug)
        )
        return q.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> ContentAliasRow:
        row = await self.get_by_slug_optional(slug)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        return row

    async def get_by_id_or_slug(
        self,
        *,
        alias_id_or_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        if self.is_alias_id(alias_id_or_slug):
            return await self.get(alias_id=alias_id_or_slug, principal=principal, employee=employee)
        row = await self.get_by_slug(alias_id_or_slug)
        await self._acl.require_read_alias(principal=principal, employee=employee, alias=row)
        return alias_public(row)

    async def get(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        await self._acl.require_read_alias(principal=principal, employee=employee, alias=row)
        return alias_public(row)

    async def list_for_company(
        self,
        *,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ContentAliasRow)
            .where(ContentAliasRow.owner_company_id == company_id)
            .order_by(ContentAliasRow.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        out: list[dict[str, Any]] = []
        for row in q.scalars().all():
            if await self._acl.can_read_alias(principal=principal, employee=employee, alias=row):
                out.append(alias_public(row))
        return out

    async def patch(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        label: str | None = None,
        description: str | None = None,
        metadata: dict | None = None,
        status: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        await self._acl.require_write_alias(principal=principal, employee=employee, alias=row)
        if label is not None:
            row.label = label
        if description is not None:
            row.description = description
        if metadata is not None:
            row.metadata_ = metadata
        if status is not None:
            row.status = status
        if visibility is not None:
            row.visibility = visibility
        await self._session.commit()
        await self._session.refresh(row)
        return alias_public(row)

    async def delete(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        row = await self._session.get(ContentAliasRow, alias_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        await self._acl.require_write_alias(principal=principal, employee=employee, alias=row)
        public = alias_public(row)
        await self._session.delete(row)
        await self._session.commit()
        return {"deleted": True, **public}
