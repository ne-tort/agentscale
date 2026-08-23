"""CabinetInstance lifecycle (L06)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.identity.service import EntitlementService
from prodavan.domain.cabinets import CabinetStatus, schema_name_for_instance
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

logger = logging.getLogger(__name__)


def _public(row: CabinetInstanceRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "schema_name": row.schema_name,
        "owner_employee_id": row.owner_employee_id,
        "company_id": row.company_id,
        "base_template": row.base_template,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class CabinetInstanceService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        provisioner: SchemaProvisioner | None = None,
    ) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._provisioner = provisioner or SchemaProvisioner()

    async def create_from_base(
        self,
        *,
        name: str,
        company_id: str,
        employee: EmployeeRow,
        base_template: str = "base",
    ) -> dict:
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        await EntitlementService(self._session).require_membership(employee.id, company_id)
        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        row = CabinetInstanceRow(
            name=name.strip(),
            schema_name="pending",
            owner_employee_id=employee.id,
            company_id=company_id,
            base_template=base_template,
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row)

    async def list_for_actor(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        if principal.is_platform_admin:
            q = await self._session.execute(
                select(CabinetInstanceRow).order_by(CabinetInstanceRow.created_at.desc())
            )
            return [_public(r) for r in q.scalars().all()]
        if employee is None:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
        q = await self._session.execute(
            select(CabinetInstanceRow)
            .where(CabinetInstanceRow.owner_employee_id == employee.id)
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        return [_public(r) for r in q.scalars().all()]

    async def get(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        return _public(inst)

    async def rename(
        self,
        *,
        cabinet_id: str,
        name: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        inst.name = name.strip()
        await self._session.commit()
        await self._session.refresh(inst)
        return _public(inst)

    async def archive(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        inst.status = CabinetStatus.ARCHIVED
        await self._session.commit()
        await self._session.refresh(inst)
        # Wipe MCP package blobs (C-OBJECT-STORE); schema rows stay until hard-delete.
        wipe: dict = {"ok": False, "deleted": 0, "remaining": 0}
        try:
            from prodavan.core.infra.object_keys import cabinet_packages_prefix
            from prodavan.core.infra.object_storage_manager import ensure_object_storage

            store = ensure_object_storage()
            prefix = cabinet_packages_prefix(cabinet_id)
            deleted = store.delete_prefix_sync(prefix)
            remaining = store.list_prefix_sync(prefix, limit=1)
            wipe = {
                "ok": len(remaining) == 0,
                "deleted": int(deleted),
                "remaining": len(remaining),
            }
            if remaining:
                logger.warning(
                    "cabinet archive: packages wipe incomplete cabinet_id=%s remaining=%s",
                    cabinet_id,
                    remaining,
                )
        except Exception:
            logger.exception("cabinet archive: packages wipe failed cabinet_id=%s", cabinet_id)
        out = _public(inst)
        out["packages_wipe"] = wipe
        return out
