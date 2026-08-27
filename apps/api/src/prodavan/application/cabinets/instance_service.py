"""CabinetInstance lifecycle — registry + cascade delete."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.cabinets.grant_service import CabinetGrantService
from prodavan.application.identity.service import EntitlementService
from prodavan.domain.cabinets import CabinetOwnerScope, CabinetStatus, schema_name_for_instance
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.ownership import is_company_registry, registry_source
from prodavan.infrastructure.cabinets.schema_provisioner import SchemaProvisioner
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow

logger = logging.getLogger(__name__)


async def _public_row(
    session: AsyncSession,
    row: CabinetInstanceRow,
    *,
    grants: CabinetGrantService,
    company_name: str | None = None,
) -> dict:
    companies = await grants.list_companies(row.id)
    company_ids = [c["company_id"] for c in companies]
    assignments_count = await grants.assignment_count(row.id)
    out = {
        "id": row.id,
        "name": row.name,
        "schema_name": row.schema_name,
        "owner_employee_id": row.owner_employee_id,
        "company_id": row.company_id,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "company_ids": company_ids,
        "companies": companies,
        "assignments_count": assignments_count,
        "writable": is_company_registry(row.owner_scope),
        "operable": True,
        "source": registry_source(owner_scope=row.owner_scope),
        "base_template": row.base_template,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if company_name is not None:
        out["company_name"] = company_name
    elif companies:
        out["company_name"] = companies[0].get("company_name")
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
        self._grants = CabinetGrantService(session)
        self._provisioner = provisioner or SchemaProvisioner()

    async def create_for_admin(
        self,
        *,
        name: str,
        company_id: str | None = None,
        company_ids: list[str] | None = None,
    ) -> dict:
        """Platform Admin creates a platform-owned cabinet with company grants."""
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        ids = list(company_ids or [])
        if company_id and company_id not in ids:
            ids.insert(0, company_id)
        if not ids:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="company_ids required",
            )
        primary = ids[0]
        company = await self._session.get(CompanyRow, primary)
        if company is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="company not found")
        await CompanyQuotaService(self._session).assert_can_create_cabinet(primary)

        row = CabinetInstanceRow(
            name=name.strip(),
            schema_name="pending",
            owner_employee_id=None,
            company_id=primary,
            owner_scope=CabinetOwnerScope.PLATFORM,
            owner_company_id=None,
            base_template="base",
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        await self._grants.replace_company_grants(row.id, ids)
        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, grants=self._grants, company_name=company.name)

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
            owner_scope=CabinetOwnerScope.COMPANY,
            owner_company_id=company_id,
            base_template=base_template,
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        await self._grants.replace_company_grants(row.id, [company_id])
        await self._grants.assign_employee(
            cabinet_id=row.id,
            employee_id=employee.id,
            company_id=company_id,
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, grants=self._grants)

    async def list_all_admin(self) -> list[dict]:
        q = await self._session.execute(
            select(CabinetInstanceRow)
            .where(CabinetInstanceRow.status != CabinetStatus.DELETED)
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        out: list[dict] = []
        for row in q.scalars().all():
            out.append(await _public_row(self._session, row, grants=self._grants))
        return out

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
            .join(
                CabinetEmployeeAssignmentRow,
                CabinetEmployeeAssignmentRow.cabinet_id == CabinetInstanceRow.id,
            )
            .where(
                CabinetEmployeeAssignmentRow.employee_id == employee.id,
                CabinetEmployeeAssignmentRow.status == "active",
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        out: list[dict] = []
        for row in q.scalars().unique().all():
            out.append(await _public_row(self._session, row, grants=self._grants))
        return out

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
        return await _public_row(self._session, inst, grants=self._grants)

    async def get_admin(self, *, cabinet_id: str) -> dict:
        inst = await self._access.get_instance(cabinet_id)
        return await _public_row(self._session, inst, grants=self._grants)

    async def update_admin(
        self,
        *,
        cabinet_id: str,
        name: str | None = None,
        company_id: str | None = None,
        company_ids: list[str] | None = None,
    ) -> dict:
        inst = await self._access.get_instance(cabinet_id)
        if name is not None:
            if not name.strip():
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
            inst.name = name.strip()
        if company_ids is not None:
            await self._grants.replace_company_grants(cabinet_id, company_ids)
        elif company_id is not None:
            await self._grants.replace_company_grants(cabinet_id, [company_id])
        await self._session.commit()
        await self._session.refresh(inst)
        return await _public_row(self._session, inst, grants=self._grants)

    async def rename(
        self,
        *,
        cabinet_id: str,
        name: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            registry_write=True,
        )
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        inst.name = name.strip()
        await self._session.commit()
        await self._session.refresh(inst)
        return await _public_row(self._session, inst, grants=self._grants)

    async def archive(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            registry_write=True,
        )
        inst.status = CabinetStatus.ARCHIVED
        await self._session.commit()
        await self._session.refresh(inst)
        return await _public_row(self._session, inst, grants=self._grants)

    async def soft_delete(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        """Soft-delete cabinet: hide + soft-delete projects (no wipe, schema keep)."""
        from prodavan.application.projects.platform_event_service import PlatformEventService
        from prodavan.application.projects.project_service import ProjectService
        from prodavan.domain.projects import ProjectStatus
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            registry_write=True,
            allow_archived_write=True,
        )
        if inst.status == CabinetStatus.DELETED:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Cabinet not found")

        projects = ProjectService(self._session)
        proj_q = await self._session.execute(
            select(ProjectRow.id).where(
                ProjectRow.cabinet_id == cabinet_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        projects_soft_deleted: list[str] = []
        for project_id in proj_q.scalars().all():
            await projects.delete(
                project_id=project_id,
                principal=principal,
                employee=employee,
                purge_workspace=False,
            )
            projects_soft_deleted.append(project_id)

        inst.status = CabinetStatus.DELETED
        company_id = inst.company_id or inst.owner_company_id
        if company_id:
            await PlatformEventService(self._session).emit(
                event_type="cabinet.soft_deleted",
                company_id=company_id,
                cabinet_id=inst.id,
                principal=principal,
                payload={"cabinet_id": inst.id, "projects": projects_soft_deleted},
            )
        await self._session.commit()
        return {
            "id": cabinet_id,
            "deleted": True,
            "soft": True,
            "projects_soft_deleted": projects_soft_deleted,
        }

    async def restore(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
    ) -> dict:
        """Restore soft-deleted cabinet → archived (paused). Projects stay deleted until restored."""
        from prodavan.application.projects.platform_event_service import PlatformEventService

        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            registry_write=True,
            allow_archived_write=True,
            allow_deleted=True,
        )
        if inst.status != CabinetStatus.DELETED:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="cabinet is not soft-deleted",
            )
        inst.status = CabinetStatus.ARCHIVED
        company_id = inst.company_id or inst.owner_company_id
        if company_id:
            await PlatformEventService(self._session).emit(
                event_type="cabinet.restored",
                company_id=company_id,
                cabinet_id=inst.id,
                principal=principal,
            )
        await self._session.commit()
        await self._session.refresh(inst)
        out = await _public_row(self._session, inst, grants=self._grants)
        out["restored"] = True
        return out

    async def delete_with_cascade(self, *, cabinet_id: str) -> dict:
        """Hard-purge: wipe projects + drop schema + delete row (Admin recycle)."""
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
                "cabinet purge: drop_schema failed cabinet_id=%s schema=%s",
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
            "purged": True,
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
        """Purge after soft-delete (or archive for platform admin). Default employee path: soft_delete."""
        inst = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=True,
            registry_write=True,
            allow_archived_write=True,
            allow_deleted=True,
        )
        if inst.status != CabinetStatus.DELETED and not principal.is_platform_admin:
            if inst.status != CabinetStatus.ARCHIVED:
                raise AppError(
                    code="CABINET_NOT_SOFT_DELETED",
                    title="Cabinet not soft-deleted",
                    status=409,
                    detail="soft-delete the cabinet before purge",
                )
        return await self.delete_with_cascade(cabinet_id=cabinet_id)

    async def assign_employee(
        self,
        *,
        cabinet_id: str,
        company_id: str,
        employee_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        await self._grants.assign_employee(
            cabinet_id=cabinet_id,
            employee_id=employee_id,
            company_id=company_id,
        )
        await self._session.commit()
        return {"cabinet_id": cabinet_id, "employee_id": employee_id, "status": "active"}

    async def revoke_employee_assignment(
        self,
        *,
        cabinet_id: str,
        company_id: str,
        employee_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        await self._grants.revoke_employee(cabinet_id=cabinet_id, employee_id=employee_id)
        await self._session.commit()
        return {"cabinet_id": cabinet_id, "employee_id": employee_id, "status": "revoked"}

    async def list_assignments_for_company(
        self,
        *,
        cabinet_id: str,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        if not await self._grants.has_active_company_grant(cabinet_id, company_id):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")
        return await self._grants.list_assignments(cabinet_id)
