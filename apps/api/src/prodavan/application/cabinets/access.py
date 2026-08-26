"""Cabinet access control — peer isolation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class CabinetAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_instance(self, cabinet_id: str) -> CabinetInstanceRow:
        row = await self._session.get(CabinetInstanceRow, cabinet_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Cabinet not found")
        return row

    async def require_access(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
        allow_archived_write: bool = False,
    ) -> CabinetInstanceRow:
        inst = await self.get_instance(cabinet_id)
        if principal.is_platform_admin:
            return inst
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        if inst.owner_employee_id is None or inst.owner_employee_id != employee.id:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="peer cabinet access denied",
            )
        if inst.status == CabinetStatus.ARCHIVED and write and not allow_archived_write:
            raise AppError(
                code="CABINET_ARCHIVED",
                title="Cabinet archived",
                status=409,
                detail="cabinet is archived",
            )
        return inst
