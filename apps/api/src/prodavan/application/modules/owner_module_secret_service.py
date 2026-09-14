"""Upload platform/company module secrets for secret_ref columns."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_instance_service import (
    OWNER_COMPANY,
    OWNER_PLATFORM,
    PLATFORM_OWNER_ID,
)
from prodavan.application.modules.module_service import ModuleService
from prodavan.application.modules.company_module_service import CompanyModuleService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.secrets.owner_module_secret_store import (
    get_owner_module_secret_store,
    secret_ref_prefix,
)


class OwnerModuleSecretService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._secrets = get_owner_module_secret_store()

    async def upload_platform_secret(
        self,
        *,
        module_id: str,
        secret: str,
        label: str | None,
    ) -> dict:
        await ModuleService(self._session)._get_row(module_id)
        ref = self._secrets.put(
            owner_kind=OWNER_PLATFORM,
            owner_id=PLATFORM_OWNER_ID,
            secret=secret,
        )
        return {
            "secret_ref": ref,
            "secret_ref_prefix": secret_ref_prefix(ref),
            "label": (label or "").strip(),
            "created_at": datetime.now(UTC).isoformat(),
            "owner_kind": OWNER_PLATFORM,
            "owner_id": PLATFORM_OWNER_ID,
        }

    async def upload_company_secret(
        self,
        *,
        company_id: str,
        module_id: str,
        secret: str,
        label: str | None,
    ) -> dict:
        if not company_id.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company_id required",
            )
        await CompanyModuleService(self._session).get_for_company(
            company_id=company_id, module_id=module_id
        )
        ref = self._secrets.put(
            owner_kind=OWNER_COMPANY,
            owner_id=company_id,
            secret=secret,
        )
        return {
            "secret_ref": ref,
            "secret_ref_prefix": secret_ref_prefix(ref),
            "label": (label or "").strip(),
            "created_at": datetime.now(UTC).isoformat(),
            "owner_kind": OWNER_COMPANY,
            "owner_id": company_id,
        }
