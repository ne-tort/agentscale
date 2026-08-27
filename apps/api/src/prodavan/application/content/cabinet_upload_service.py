"""Cabinet-scoped content upload — returns FileRef for module rows."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.content.upload_service import UploadService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class CabinetContentUploadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._upload = UploadService(session)

    async def upload_for_cabinet(
        self,
        *,
        cabinet_id: str,
        data: bytes,
        filename: str,
        mime: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        owner_company_id = inst.company_id or inst.owner_company_id
        if not owner_company_id:
            owner_company_id = await self._platform_owner_company(inst)
        if not owner_company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="cabinet has no company scope for content upload",
            )
        asset_id, version_id = await self._upload.upload_bytes_as_asset(
            data=data,
            owner_company_id=owner_company_id,
            principal=principal,
            employee=employee,
            mime=mime,
            title=filename,
            link_kind="cabinet",
            link_id=cabinet_id,
        )
        from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow

        ver = await self._session.get(ContentBlobVersionRow, version_id)
        storage_key = ver.storage_key if ver else None
        return {
            "asset_id": asset_id,
            "version_id": version_id,
            "blob_version_id": version_id,
            "storage_key": storage_key,
            "filename": filename,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    async def _platform_owner_company(self, inst: CabinetInstanceRow) -> str | None:
        if inst.company_grant_scope == "all":
            from sqlalchemy import select

            from prodavan.infrastructure.persistence.models.identity import CompanyRow

            q = await self._session.execute(select(CompanyRow.id).limit(1))
            return q.scalar_one_or_none()
        return None
