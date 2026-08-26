"""CabinetInstance lifecycle — registry + cascade delete."""

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
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

logger = logging.getLogger(__name__)


def _public(row: CabinetInstanceRow, *, company_name: str | None = None) -> dict:
    out = {
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
    if company_name is not None:
        out["company_name"] = company_name
    return out


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

    async def create_for_admin(self, *, name: str, company_id: str) -> dict:
        """Platform Admin creates a cabinet bound to a company (no employee owner)."""
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        company = await self._session.get(CompanyRow, company_id)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="company not found")
        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        row = CabinetInstanceRow(
            name=name.strip(),
            schema_name="pending",
            owner_employee_id=None,
            company_id=company_id,
            base_template="base",
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        await self._session.commit()
        await self._session.refresh(row)
        return _public(row, company_name=company.name)

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

    async def list_all_admin(self) -> list[dict]:
        q = await self._session.execute(
            select(CabinetInstanceRow, CompanyRow.name)
            .outerjoin(CompanyRow, CompanyRow.id == CabinetInstanceRow.company_id)
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        return [_public(row, company_name=cname) for row, cname in q.all()]

    async def list_for_actor(
        self,
        *,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        if principal.is_platform_admin:
            return await self.list_all_admin()
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
        company = await self._session.get(CompanyRow, inst.company_id)
        return _public(inst, company_name=company.name if company else None)

    async def get_admin(self, *, cabinet_id: str) -> dict:
        inst = await self._access.get_instance(cabinet_id)
        company = await self._session.get(CompanyRow, inst.company_id)
        return _public(inst, company_name=company.name if company else None)

    async def update_admin(
        self,
        *,
        cabinet_id: str,
        name: str | None = None,
        company_id: str | None = None,
    ) -> dict:
        inst = await self._access.get_instance(cabinet_id)
        if name is not None:
            if not name.strip():
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
            inst.name = name.strip()
        if company_id is not None:
            company = await self._session.get(CompanyRow, company_id)
            if company is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="company not found")
            inst.company_id = company_id
        await self._session.commit()
        await self._session.refresh(inst)
        company = await self._session.get(CompanyRow, inst.company_id)
        return _public(inst, company_name=company.name if company else None)

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
        return _public(inst)

    async def delete_with_cascade(self, *, cabinet_id: str) -> dict:
        """Wipe projects + drop schema + delete row (Admin; no archive required)."""
        from prodavan.application.projects.project_wipe import wipe_project_tree
        from prodavan.core.jobs.enqueue import enqueue_wipe_project_tree
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        inst = await self._access.get_instance(cabinet_id)

        projects_q = await self._session.execute(
            select(ProjectRow.id, ProjectRow.workspace_key).where(ProjectRow.cabinet_id == cabinet_id)
        )
        project_rows = list(projects_q.all())
        project_wipes: list[dict] = []
        for _pid, workspace_key in project_rows:
            wipe = wipe_project_tree(workspace_key)
            if not wipe.get("ok"):
                retry = enqueue_wipe_project_tree(workspace_key)
                wipe["retry_enqueued"] = bool(retry.get("enqueued"))
                wipe["retry"] = retry
            project_wipes.append(wipe)

        schema_name = inst.schema_name
        try:
            await self._provisioner.drop_schema(self._session, schema_name=schema_name)
        except Exception:
            logger.exception(
                "cabinet delete: drop_schema failed cabinet_id=%s schema=%s",
                cabinet_id,
                schema_name,
            )
            raise AppError(
                code="SCHEMA_DROP_FAILED",
                title="Schema drop failed",
                status=500,
                detail=f"failed to drop schema {schema_name}",
            ) from None

        await self._session.delete(inst)
        await self._session.commit()
        return {
            "deleted": True,
            "id": cabinet_id,
            "schema_name": schema_name,
            "schema_dropped": True,
            "project_wipes": project_wipes,
            "projects_purged": len(project_rows),
        }

    async def hard_delete(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        """Employee/admin hard-delete: archive required unless platform admin."""
        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            allow_archived_write=True,
        )
        if not principal.is_platform_admin and inst.status != CabinetStatus.ARCHIVED:
            raise AppError(
                code="CABINET_NOT_ARCHIVED",
                title="Cabinet not archived",
                status=409,
                detail="archive the cabinet before hard-delete",
            )
        return await self.delete_with_cascade(cabinet_id=cabinet_id)
