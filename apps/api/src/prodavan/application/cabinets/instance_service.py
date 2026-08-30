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
from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
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
    from prodavan.application.modules.module_binding_service import ModuleBindingService

    module_ids = await ModuleBindingService(session).list_module_ids_for_cabinet(row.id)
    from prodavan.application.admin.quota_service import CompanyQuotaService

    projects_count = await CompanyQuotaService(session).count_active_projects_in_cabinet(row.id)
    out = {
        "id": row.id,
        "name": row.name,
        "schema_name": row.schema_name,
        "owner_employee_id": row.owner_employee_id,
        "company_id": row.company_id,
        "owner_scope": row.owner_scope,
        "owner_company_id": row.owner_company_id,
        "template_cabinet_id": row.template_cabinet_id,
        "max_projects": row.max_projects,
        "projects_count": projects_count,
        "company_ids": company_ids,
        "companies": companies,
        "module_ids": module_ids,
        "module_bindings_count": len(module_ids),
        "assignments_count": assignments_count,
        "writable": is_company_registry(row.owner_scope),
        "operable": True,
        "source": registry_source(owner_scope=row.owner_scope),
        "base_template": row.base_template,
        "company_grant_scope": row.company_grant_scope,
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
        """Platform Admin creates a platform-owned cabinet; company grants optional."""
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        ids = list(company_ids or [])
        if company_id and company_id not in ids:
            ids.insert(0, company_id)
        if ids:
            for cid in ids:
                co = await self._session.get(CompanyRow, cid)
                if co is None:
                    raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="company not found")

        row = CabinetInstanceRow(
            name=name.strip(),
            schema_name="pending",
            owner_employee_id=None,
            company_id=None,
            owner_scope=CabinetOwnerScope.PLATFORM,
            owner_company_id=None,
            base_template="base",
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)
        from prodavan.application.relations.commands import RelationsCommand

        if ids:
            await RelationsCommand(self._session).replace_cabinet_company_grants(row.id, ids)
            for cid in ids:
                await self.provision_company_copy_from_template(template_id=row.id, company_id=cid)
        from prodavan.application.platform.bootstrap_service import PlatformBootstrapService

        await PlatformBootstrapService(self._session).apply_default_modules_for_cabinet(
            row.id, base_template=row.base_template
        )
        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, grants=self._grants)

    async def provision_company_copy_from_template(
        self,
        *,
        template_id: str,
        company_id: str,
    ) -> CabinetInstanceRow:
        """Materialize company-owned workspace from platform template (one copy per company)."""
        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.application.relations.commands import RelationsCommand

        existing_q = await self._session.execute(
            select(CabinetInstanceRow).where(
                CabinetInstanceRow.template_cabinet_id == template_id,
                CabinetInstanceRow.owner_company_id == company_id,
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
        )
        existing = existing_q.scalar_one_or_none()
        if existing is not None:
            return existing

        template = await self._access.get_instance(template_id)
        if template.owner_scope != CabinetOwnerScope.PLATFORM:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="only platform cabinets are templates",
            )

        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        row = CabinetInstanceRow(
            name=template.name,
            schema_name="pending",
            owner_employee_id=None,
            company_id=company_id,
            owner_scope=CabinetOwnerScope.COMPANY,
            owner_company_id=company_id,
            base_template=template.base_template,
            template_cabinet_id=template_id,
            max_projects=template.max_projects,
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)

        rel = RelationsCommand(self._session)
        await rel.replace_cabinet_company_grants(row.id, [company_id])

        bindings = ModuleBindingService(self._session)
        module_ids = await bindings.list_module_ids_for_cabinet(template_id)
        if module_ids:
            await bindings.replace_module_bindings_for_cabinet(row.id, module_ids)

        from prodavan.application.platform.bootstrap_service import PlatformBootstrapService

        if not module_ids:
            await PlatformBootstrapService(self._session).apply_default_modules_for_cabinet(
                row.id, base_template=row.base_template
            )

        await self._session.flush()
        return row

    async def create_from_base(
        self,
        *,
        name: str,
        company_id: str,
        principal: Principal,
        employee: EmployeeRow | None = None,
        base_template: str = "base",
    ) -> dict:
        if not name.strip():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        if employee is not None:
            await EntitlementService(self._session).require_membership(employee.id, company_id)
        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        row = CabinetInstanceRow(
            name=name.strip(),
            schema_name="pending",
            owner_employee_id=employee.id if employee is not None else None,
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
        from prodavan.application.relations.commands import RelationsCommand

        rel = RelationsCommand(self._session)
        await rel.replace_cabinet_company_grants(row.id, [company_id])
        if employee is not None:
            await rel.assign_employee_to_cabinet(
                cabinet_id=row.id,
                employee_id=employee.id,
                company_id=company_id,
            )
        from prodavan.application.platform.bootstrap_service import PlatformBootstrapService

        await PlatformBootstrapService(self._session).apply_default_modules_for_cabinet(
            row.id, base_template=row.base_template
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
        from prodavan.domain.lifecycle import cabinet_is_soft_deleted

        inst = await self._access.get_instance(cabinet_id)
        if cabinet_is_soft_deleted(inst):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Cabinet not found")
        return await _public_row(self._session, inst, grants=self._grants)

    async def update_admin(
        self,
        *,
        cabinet_id: str,
        name: str | None = None,
        company_id: str | None = None,
        company_ids: list[str] | None = None,
        module_ids: list[str] | None = None,
        company_grant_scope: str | None = None,
        max_projects: int | None = None,
        clear_max_projects: bool = False,
    ) -> dict:
        inst = await self._access.get_instance(cabinet_id)
        if name is not None:
            if not name.strip():
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
            inst.name = name.strip()
        if company_ids is not None:
            from prodavan.application.relations.commands import RelationsCommand

            await RelationsCommand(self._session).replace_cabinet_company_grants(
                cabinet_id, company_ids
            )
            if inst.owner_scope == CabinetOwnerScope.PLATFORM:
                for cid in company_ids:
                    await self.provision_company_copy_from_template(
                        template_id=cabinet_id, company_id=cid
                    )
        elif company_id is not None:
            from prodavan.application.relations.commands import RelationsCommand

            await RelationsCommand(self._session).replace_cabinet_company_grants(
                cabinet_id, [company_id]
            )
            if inst.owner_scope == CabinetOwnerScope.PLATFORM:
                await self.provision_company_copy_from_template(
                    template_id=cabinet_id, company_id=company_id
                )
        if module_ids is not None:
            from prodavan.application.modules.module_binding_service import ModuleBindingService

            await ModuleBindingService(self._session).replace_module_bindings_for_cabinet(
                cabinet_id, module_ids
            )
        if company_grant_scope is not None:
            scope = company_grant_scope.strip().lower()
            if scope not in (
                CabinetCompanyGrantScope.SELECTED,
                CabinetCompanyGrantScope.ALL,
            ):
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="company_grant_scope must be selected or all",
                )
            inst.company_grant_scope = scope
        if clear_max_projects:
            inst.max_projects = None
        elif max_projects is not None:
            if max_projects < 1:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="max_projects must be >= 1",
                )
            inst.max_projects = max_projects
        if inst.owner_scope == CabinetOwnerScope.PLATFORM and (
            clear_max_projects or max_projects is not None
        ):
            copies_q = await self._session.execute(
                select(CabinetInstanceRow).where(
                    CabinetInstanceRow.template_cabinet_id == cabinet_id,
                    CabinetInstanceRow.status != CabinetStatus.DELETED,
                )
            )
            limit = None if clear_max_projects else inst.max_projects
            for copy in copies_q.scalars().all():
                copy.max_projects = limit
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
        from prodavan.application.project_service import ProjectCommand, ProjectQuery
        from prodavan.application.projects.platform_event_service import PlatformEventService
        from prodavan.domain.projects import ProjectStatus

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

        projects = ProjectCommand(self._session)
        query = ProjectQuery(self._session)
        projects_soft_deleted: list[str] = []
        for project_id in await query.list_ids(
            cabinet_id=cabinet_id, exclude_status=ProjectStatus.DELETED
        ):
            await projects.delete(
                project_id=project_id,
                principal=principal,
                employee=employee,
                purge_workspace=False,
                skip_access=True,
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
        from prodavan.application.project_service import ProjectCommand, ProjectQuery
        from prodavan.application.projects.project_wipe import wipe_project_tree
        from prodavan.core.jobs.enqueue import enqueue_wipe_project_tree

        inst = await self._access.get_instance(cabinet_id)

        project_cmd = ProjectCommand(self._session)
        for ref in await ProjectQuery(self._session).list_workspace_refs_for_cabinet(cabinet_id):
            await project_cmd.stop_runtime_system(project_id=ref["project_id"], reason="purge")
        await self._session.flush()

        project_wipes: list[dict] = []
        for ref in await ProjectQuery(self._session).list_workspace_refs_for_cabinet(cabinet_id):
            workspace_key = ref["workspace_key"]
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
            "projects_purged": len(project_wipes),
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
        from prodavan.application.relations.commands import RelationsCommand

        await RelationsCommand(self._session).assign_employee_to_cabinet(
            cabinet_id=cabinet_id,
            company_id=company_id,
            employee_id=employee_id,
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
        from prodavan.application.relations.commands import RelationsCommand

        await RelationsCommand(self._session).revoke_employee_from_cabinet(
            cabinet_id=cabinet_id,
            employee_id=employee_id,
            company_id=company_id,
        )
        await self._session.commit()
        return {"cabinet_id": cabinet_id, "employee_id": employee_id, "status": "revoked"}

    async def copy_cabinet(
        self,
        *,
        cabinet_id: str,
        company_id: str,
        name: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        """Duplicate cabinet with module bindings + employee assignments (no projects)."""
        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.application.relations.commands import RelationsCommand

        source = await self._access.require_access(
            cabinet_id=cabinet_id,
            principal=principal,
            employee=employee,
            write=False,
        )
        await EntitlementService(self._session).require_company_actor(
            principal, company_id, employee=employee
        )
        if not await self._grants.has_active_company_grant(cabinet_id, company_id):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="cabinet not found")

        copy_name = (name or f"{source.name} (copy)").strip()
        if not copy_name:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="name required")
        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        row = CabinetInstanceRow(
            name=copy_name,
            schema_name="pending",
            owner_employee_id=employee.id if employee is not None else source.owner_employee_id,
            company_id=company_id,
            owner_scope=CabinetOwnerScope.COMPANY,
            owner_company_id=company_id,
            base_template=source.base_template,
            template_cabinet_id=source.template_cabinet_id or (
                source.id if source.owner_scope == CabinetOwnerScope.PLATFORM else None
            ),
            max_projects=source.max_projects,
            status=CabinetStatus.ACTIVE,
        )
        self._session.add(row)
        await self._session.flush()
        row.schema_name = schema_name_for_instance(row.id)
        await self._provisioner.provision(self._session, instance_id=row.id)

        rel = RelationsCommand(self._session)
        await rel.replace_cabinet_company_grants(row.id, [company_id])

        bindings = ModuleBindingService(self._session)
        module_ids = await bindings.list_module_ids_for_cabinet(cabinet_id)
        if module_ids:
            await bindings.replace_module_bindings_for_cabinet(row.id, module_ids)

        for assignment in await self._grants.list_assignments(cabinet_id):
            emp_id = assignment["employee_id"]
            try:
                await self._grants.assign_employee(
                    cabinet_id=row.id,
                    employee_id=emp_id,
                    company_id=company_id,
                )
            except AppError:
                continue

        owner_employee_id = employee.id if employee is not None else source.owner_employee_id
        if owner_employee_id is not None:
            try:
                await rel.assign_employee_to_cabinet(
                    cabinet_id=row.id,
                    employee_id=owner_employee_id,
                    company_id=company_id,
                )
            except AppError:
                pass

        await self._session.commit()
        await self._session.refresh(row)
        return await _public_row(self._session, row, grants=self._grants)

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
