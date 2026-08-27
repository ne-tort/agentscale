"""Cabinet access control — grants + owner_scope."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.grant_service import CabinetGrantService
from prodavan.application.identity.service import EntitlementService
from prodavan.application.relations.query import RelationsQuery
from prodavan.domain.cabinets import CabinetOwnerScope, CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class CabinetAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._grants = CabinetGrantService(session)
        self._relations = RelationsQuery(session)
        self._entitlements = EntitlementService(session)

    async def get_instance(self, cabinet_id: str) -> CabinetInstanceRow:
        row = await self._session.get(CabinetInstanceRow, cabinet_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Cabinet not found")
        return row

    async def _company_actor_company_id(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
        company_id: str | None,
    ) -> str | None:
        if company_id:
            return company_id
        if principal.is_company_principal and principal.username:
            return principal.username
        return None

    async def _has_company_grant_access(
        self,
        *,
        inst: CabinetInstanceRow,
        principal: Principal,
        employee: EmployeeRow | None,
        company_id: str | None,
    ) -> bool:
        actor_company = await self._company_actor_company_id(
            principal=principal, employee=employee, company_id=company_id
        )
        if actor_company and await self._relations.has_cabinet_company_grant(
            cabinet_id=inst.id, company_id=actor_company
        ):
            return True
        if employee is not None and inst.owner_company_id:
            if await self._relations.has_cabinet_company_grant(
                cabinet_id=inst.id, company_id=inst.owner_company_id
            ):
                try:
                    await self._entitlements.require_membership(employee.id, inst.owner_company_id)
                    return True
                except AppError:
                    pass
        return False

    async def _can_registry_write(
        self,
        *,
        inst: CabinetInstanceRow,
        principal: Principal,
        employee: EmployeeRow | None,
        company_id: str | None,
    ) -> bool:
        if principal.is_platform_admin:
            return True
        if inst.owner_scope != CabinetOwnerScope.COMPANY:
            return False
        actor_company = await self._company_actor_company_id(
            principal=principal, employee=employee, company_id=company_id
        )
        if actor_company and inst.owner_company_id == actor_company:
            return await self._relations.has_cabinet_company_grant(
                cabinet_id=inst.id, company_id=actor_company
            )
        if employee is not None and inst.owner_company_id:
            try:
                await self._entitlements.require_company_admin(employee.id, inst.owner_company_id)
                return await self._relations.has_cabinet_company_grant(
                    cabinet_id=inst.id, company_id=inst.owner_company_id
                )
            except AppError:
                return False
        return False

    async def require_access(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
        registry_write: bool = False,
        allow_archived_write: bool = False,
        allow_deleted: bool = False,
        company_id: str | None = None,
    ) -> CabinetInstanceRow:
        from prodavan.domain.lifecycle import cabinet_is_soft_deleted

        inst = await self.get_instance(cabinet_id)
        if cabinet_is_soft_deleted(inst) and not allow_deleted:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Cabinet not found")
        if principal.is_platform_admin:
            if inst.status == CabinetStatus.ARCHIVED and write and not allow_archived_write:
                raise AppError(
                    code="CABINET_ARCHIVED",
                    title="Cabinet archived",
                    status=409,
                    detail="cabinet is archived",
                )
            return inst

        if registry_write:
            if not await self._can_registry_write(
                inst=inst, principal=principal, employee=employee, company_id=company_id
            ):
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="cabinet registry is read-only",
                )
        elif write:
            allowed = False
            if employee is not None and await self._relations.has_cabinet_assignment(
                cabinet_id=cabinet_id, employee_id=employee.id
            ):
                allowed = True
            if await self._has_company_grant_access(
                inst=inst, principal=principal, employee=employee, company_id=company_id
            ):
                allowed = True
            if not allowed:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="cabinet access denied",
                )
        else:
            allowed = False
            if employee is not None and await self._relations.has_cabinet_assignment(
                cabinet_id=cabinet_id, employee_id=employee.id
            ):
                allowed = True
            if await self._has_company_grant_access(
                inst=inst, principal=principal, employee=employee, company_id=company_id
            ):
                allowed = True
            if not allowed:
                raise AppError(
                    code="FORBIDDEN",
                    title="Forbidden",
                    status=403,
                    detail="cabinet access denied",
                )

        if inst.status == CabinetStatus.ARCHIVED and (write or registry_write) and not allow_archived_write:
            raise AppError(
                code="CABINET_ARCHIVED",
                title="Cabinet archived",
                status=409,
                detail="cabinet is archived",
            )
        return inst
