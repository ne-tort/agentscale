"""Upload cabinet module secrets for secret_ref columns."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.secrets.cabinet_secret_store import (
    get_cabinet_secret_store,
    secret_ref_prefix,
)


class CabinetModuleSecretService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._secrets = get_cabinet_secret_store()

    async def upload_secret(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        secret: str,
        label: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)

        ref = self._secrets.put(cabinet_id=cabinet_id, secret=secret)
        return {
            "secret_ref": ref,
            "secret_ref_prefix": secret_ref_prefix(ref),
            "label": (label or "").strip(),
            "created_at": datetime.now(UTC).isoformat(),
        }

    async def _require_module_binding(self, *, cabinet_id: str, module_id: str) -> None:
        from sqlalchemy import select

        from prodavan.infrastructure.persistence.models.modules import ModuleCabinetBindingRow

        q = await self._session.execute(
            select(ModuleCabinetBindingRow.module_id).where(
                ModuleCabinetBindingRow.cabinet_id == cabinet_id,
                ModuleCabinetBindingRow.module_id == module_id,
            )
        )
        if q.scalar_one_or_none() is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="module not bound")
