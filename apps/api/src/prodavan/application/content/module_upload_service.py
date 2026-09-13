"""Module-scoped content upload for platform/company instance editors."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.upload_service import UploadService
from prodavan.application.modules.company_module_service import CompanyModuleService
from prodavan.application.modules.module_service import ModuleService
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.content import ContentBlobVersionRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


def _file_ref(*, asset_id: str, version_id: str, storage_key: str | None, filename: str, data: bytes) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "version_id": version_id,
        "blob_version_id": version_id,
        "storage_key": storage_key,
        "filename": filename,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class ModuleContentUploadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._upload = UploadService(session)

    async def upload_for_platform_module(
        self,
        *,
        module_id: str,
        data: bytes,
        filename: str,
        mime: str | None,
        principal: Principal,
    ) -> dict[str, Any]:
        await ModuleService(self._session).get_admin(module_id=module_id)
        asset_id, version_id = await self._upload.upload_bytes_as_asset(
            data=data,
            owner_company_id=None,
            principal=principal,
            employee=None,
            mime=mime,
            title=filename,
            link_kind=None,
            link_id=None,
        )
        ver = await self._session.get(ContentBlobVersionRow, version_id)
        return _file_ref(
            asset_id=asset_id,
            version_id=version_id,
            storage_key=ver.storage_key if ver else None,
            filename=filename,
            data=data,
        )

    async def upload_for_company_module(
        self,
        *,
        company_id: str,
        module_id: str,
        data: bytes,
        filename: str,
        mime: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        await CompanyModuleService(self._session).get_for_company(
            company_id=company_id, module_id=module_id
        )
        asset_id, version_id = await self._upload.upload_bytes_as_asset(
            data=data,
            owner_company_id=company_id,
            principal=principal,
            employee=employee,
            mime=mime,
            title=filename,
            link_kind=None,
            link_id=None,
        )
        ver = await self._session.get(ContentBlobVersionRow, version_id)
        return _file_ref(
            asset_id=asset_id,
            version_id=version_id,
            storage_key=ver.storage_key if ver else None,
            filename=filename,
            data=data,
        )
