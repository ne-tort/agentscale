"""Project-scoped module runtime — leaf instances for hubs + data CRUD."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_instance_service import (
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    validate_row_with_columns,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectModuleBindingRow, ProjectRow


class ProjectRuntimeModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._instances = ModuleInstanceService(session)
        self._template_meta = ModuleMetaDocumentService(session)

    async def _require_project(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool,
    ) -> ProjectRow:
        # Lazy import: project_service package __init__ pulls command → circular with materialize.
        from prodavan.application.project_service.access import ProjectAccessPolicy

        return await ProjectAccessPolicy(self._session).require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=write,
            allow_paused=True,
        )

    async def _enabled_module_ids(self, project: ProjectRow) -> set[str]:
        q = await self._session.execute(
            select(ProjectModuleBindingRow.module_id).where(
                ProjectModuleBindingRow.project_id == project.id
            )
        )
        bound = set(q.scalars().all())
        if bound:
            return bound
        q2 = await self._session.execute(
            select(ModuleCabinetBindingRow.module_id).where(
                ModuleCabinetBindingRow.cabinet_id == project.cabinet_id
            )
        )
        return set(q2.scalars().all())

    async def list_modules(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict[str, Any]]:
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        await self._instances.ensure_project_instances_for_cabinet_modules(project_id=project_id)
        enabled = await self._enabled_module_ids(project)
        q = await self._session.execute(
            select(ModuleCabinetBindingRow, ModuleRow)
            .join(ModuleRow, ModuleRow.id == ModuleCabinetBindingRow.module_id)
            .where(ModuleCabinetBindingRow.cabinet_id == project.cabinet_id)
            .order_by(ModuleRow.name)
        )
        out: list[dict[str, Any]] = []
        for _bind, mod in q.all():
            if mod.id not in enabled:
                continue
            inst = await self._instances.get_instance(
                owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=mod.id
            )
            out.append(
                {
                    "id": mod.id,
                    "module_id": mod.id,
                    "name": mod.name,
                    "status": mod.status,
                    "instance_id": inst.id if inst else None,
                }
            )
        return out

    async def get_meta_document(
        self,
        *,
        project_id: str,
        module_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        # Persist fork before returning so subsequent meta slug fetches skip re-fork.
        await self._session.commit()
        try:
            doc = await self._instances.get_meta_document(instance_id=inst.id, slug=slug)
        except AppError:
            doc = await self._template_meta.get_document(module_id=module_id, slug=slug)
        return {"module_id": module_id, "instance_id": inst.id, **doc}

    async def list_data_rows(
        self,
        *,
        project_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict[str, Any]]:
        table_slug = check_table_slug(table_slug)
        await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        rows = await self._instances.list_data_rows(instance_id=inst.id, table_slug=table_slug)
        return [
            {
                "module_id": module_id,
                "instance_id": inst.id,
                **row,
            }
            for row in rows
        ]

    async def create_data_row(
        self,
        *,
        project_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = merge_column_defaults(
            columns_body=columns_body, table_slug=table_slug, body=body
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=project.cabinet_id,
        )
        created_by = employee.id if employee is not None else principal.sub
        row = await self._instances.create_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            body=body,
            created_by=created_by,
        )
        await self._session.commit()
        from prodavan.application.projects.workspace_sync_policy import (
            defer_or_schedule_project_sync,
        )

        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="project_module_instance",
        )
        remat = notification.rematerialize_alias()
        return {
            "module_id": module_id,
            "instance_id": inst.id,
            **row,
            "rematerialize": remat,
        }

    async def update_data_row(
        self,
        *,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=project.cabinet_id,
        )
        existing = await self._instances.get_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if existing is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        row = await self._instances.upsert_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            created_by=existing.get("created_by"),
        )
        await self._session.commit()
        from prodavan.application.projects.workspace_sync_policy import (
            defer_or_schedule_project_sync,
        )

        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="project_module_instance",
        )
        remat = notification.rematerialize_alias()
        return {
            "module_id": module_id,
            "instance_id": inst.id,
            **row,
            "rematerialize": remat,
        }

    async def delete_data_row(
        self,
        *,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        inst = await self._instances.ensure_project_instance(
            project_id=project_id, module_id=module_id
        )
        ok = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if not ok:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        await self._session.commit()
        from prodavan.application.projects.workspace_sync_policy import (
            defer_or_schedule_project_sync,
        )

        notification = await defer_or_schedule_project_sync(
            self._session,
            project_id=project_id,
            source="project_module_instance",
        )
        remat = notification.rematerialize_alias()
        return {"deleted": True, "row_id": row_id, "rematerialize": remat}
