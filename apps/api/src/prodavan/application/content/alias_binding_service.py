"""AliasBindingService — temporal alias ↔ asset links."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.content import (
    ContentAliasBindingRow,
    ContentAliasRow,
    ContentAssetRow,
    ContentBlobVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def _binding_public(row: ContentAliasBindingRow) -> dict[str, Any]:
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

    async def get_current(self, alias_id: str) -> ContentAliasBindingRow | None:
        q = await self._session.execute(
            select(ContentAliasBindingRow).where(
                ContentAliasBindingRow.alias_id == alias_id,
                ContentAliasBindingRow.superseded_at.is_(None),
            )
        )
        return q.scalar_one_or_none()

    async def bind(
        self,
        *,
        alias_id: str,
        asset_id: str,
        blob_version_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        alias = await self._session.get(ContentAliasRow, alias_id)
        if alias is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        asset = await self._session.get(ContentAssetRow, asset_id)
        if asset is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="asset not found")
        await self._acl.require_write_alias(principal=principal, employee=employee, alias=alias)
        await self._acl.require_read_asset(principal=principal, employee=employee, asset=asset)
        if blob_version_id:
            ver = await self._session.get(ContentBlobVersionRow, blob_version_id)
            if ver is None or ver.asset_id != asset_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="version not found")
        now = datetime.now(UTC)
        current = await self.get_current(alias_id)
        if current is not None:
            current.superseded_at = now
        row = ContentAliasBindingRow(
            alias_id=alias_id,
            asset_id=asset_id,
            blob_version_id=blob_version_id,
            bound_by=employee.id if employee else None,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return _binding_public(row)

    async def unbind(
        self,
        *,
        alias_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        alias = await self._session.get(ContentAliasRow, alias_id)
        if alias is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="alias not found")
        await self._acl.require_write_alias(principal=principal, employee=employee, alias=alias)
        current = await self.get_current(alias_id)
        if current is None:
            return {"unbound": False, "alias_id": alias_id}
        current.superseded_at = datetime.now(UTC)
        await self._session.commit()
        return {"unbound": True, "alias_id": alias_id, "binding_id": current.id}

    async def history(self, alias_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(ContentAliasBindingRow)
            .where(ContentAliasBindingRow.alias_id == alias_id)
            .order_by(ContentAliasBindingRow.effective_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return [_binding_public(r) for r in q.scalars().all()]
