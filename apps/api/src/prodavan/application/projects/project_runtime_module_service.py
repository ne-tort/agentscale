"""Project-scoped module runtime — leaf instances for hubs + data CRUD."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import (
    OWNER_PROJECT,
    ModuleInstanceService,
)
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    merge_row_patch,
    validate_row_with_columns,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.modules import ModuleBindKind, default_project_bind_kind
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)


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

    async def _sot_for_project(
        self,
        *,
        project_id: str,
        module_id: str,
        write: bool,
    ):
        from prodavan.application.modules.module_instance_service import OWNER_PROJECT
        from prodavan.domain.modules import ModuleBindKind

        binding = await ModuleBindingService(self._session).get_project_binding(
            module_id, project_id
        )
        if binding is None:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="module is not bound to project",
            )
        if binding.bind_kind == ModuleBindKind.LOCAL:
            return await self._instances.ensure_project_instance(
                project_id=project_id, module_id=module_id
            )
        if write and not binding.child_may_edit:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="global module bind is read-only for this project",
            )
        sot = await self._instances.resolve_sot_instance(
            module_id=module_id,
            owner_kind=OWNER_PROJECT,
            owner_id=project_id,
        )
        if sot is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="module SoT instance not found",
            )
        return sot

    async def _enabled_module_ids(self, project: ProjectRow) -> set[str]:
        from prodavan.application.modules.module_binding_service import ModuleBindingService

        return set(
            await ModuleBindingService(self._session).list_module_ids_for_project(project.id)
        )

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
        bindings = ModuleBindingService(self._session)
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
            mp = await bindings.get_project_binding(mod.id, project_id)
            if mp is not None:
                bind_kind = str(mp.bind_kind)
                child_may_edit = bool(mp.child_may_edit)
            else:
                bind_kind = default_project_bind_kind(mod.id).value
                child_may_edit = bind_kind == ModuleBindKind.LOCAL.value
            inst = await self._instances.get_instance(
                owner_kind=OWNER_PROJECT, owner_id=project_id, module_id=mod.id
            )
            if inst is None:
                inst = await self._instances.resolve_sot_instance(
                    module_id=mod.id,
                    owner_kind=OWNER_PROJECT,
                    owner_id=project_id,
                )
            out.append(
                {
                    "id": mod.id,
                    "module_id": mod.id,
                    "name": mod.name,
                    "status": mod.status,
                    "instance_id": inst.id if inst else None,
                    "bind_kind": bind_kind,
                    "child_may_edit": child_may_edit,
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
        inst = await self._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
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
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_list

        table_slug = check_table_slug(table_slug)
        await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=False
        )
        inst = await self._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
        filter_sid, empty = await prepare_chat_scoped_list(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
        )
        if empty:
            return []
        rows = await self._instances.list_data_rows(
            instance_id=inst.id, table_slug=table_slug, session_id=filter_sid
        )
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
        run_actions: bool = True,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_write

        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        inst = await self._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        body, stamp_sid = await prepare_chat_scoped_write(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            body=body,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
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
            session_id=stamp_sid,
        )
        await self._session.commit()
        remat: dict[str, Any] | None = None
        try:
            from prodavan.application.projects.workspace_sync_policy import (
                defer_or_schedule_project_sync,
            )

            notification = await defer_or_schedule_project_sync(
                self._session,
                project_id=project_id,
                source="project_module_instance",
            )
            remat = notification.rematerialize_alias()
        except Exception:
            logger.exception(
                "workspace sync after module row write failed project=%s module=%s",
                project_id,
                module_id,
            )
            remat = {"mode": "deferred", "error": "sync_failed"}
        out: dict[str, Any] = {
            "module_id": module_id,
            "instance_id": inst.id,
            **row,
            "rematerialize": remat,
        }
        row_id = str(row.get("row_id") or "")
        if run_actions and row_id:
            action_error: AppError | None = None
            try:
                await self._maybe_run_row_actions(
                    project_id=project_id,
                    cabinet_id=project.cabinet_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                    session_id=stamp_sid,
                )
            except AppError as exc:
                action_error = exc
            refreshed = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if refreshed is not None:
                out = {
                    "module_id": module_id,
                    "instance_id": inst.id,
                    **refreshed,
                    "rematerialize": remat,
                }
            if action_error is not None:
                raise action_error
        return out

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
        run_actions: bool = True,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from prodavan.application.modules.chat_scope_ops import prepare_chat_scoped_write

        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        project = await self._require_project(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        inst = await self._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        existing = await self._instances.get_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if existing is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        body, stamp_sid = await prepare_chat_scoped_write(
            self._instances,
            instance_id=inst.id,
            module_id=module_id,
            table_slug=table_slug,
            body=body,
            session_id=session_id,
            db=self._session,
            project_id=project_id,
            existing_row=existing,
        )
        existing_body = (
            dict(existing["body"]) if isinstance(existing.get("body"), dict) else {}
        )
        body = merge_row_patch(existing_body, body)
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=project.cabinet_id,
        )
        row = await self._instances.upsert_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            row_id=row_id,
            body=body,
            session_id=stamp_sid,
        )
        await self._session.commit()
        remat: dict[str, Any] | None = None
        try:
            from prodavan.application.projects.workspace_sync_policy import (
                defer_or_schedule_project_sync,
            )

            notification = await defer_or_schedule_project_sync(
                self._session,
                project_id=project_id,
                source="project_module_instance",
            )
            remat = notification.rematerialize_alias()
        except Exception:
            logger.exception(
                "workspace sync after module row write failed project=%s module=%s",
                project_id,
                module_id,
            )
            remat = {"mode": "deferred", "error": "sync_failed"}
        out: dict[str, Any] = {
            "module_id": module_id,
            "instance_id": inst.id,
            **row,
            "rematerialize": remat,
        }
        if run_actions:
            action_error: AppError | None = None
            try:
                await self._maybe_run_row_actions(
                    project_id=project_id,
                    cabinet_id=project.cabinet_id,
                    module_id=module_id,
                    table_slug=table_slug,
                    row_id=row_id,
                    principal=principal,
                    employee=employee,
                    previous_body=(
                        existing.get("body")
                        if isinstance(existing.get("body"), dict)
                        else None
                    ),
                    session_id=stamp_sid,
                )
            except AppError as exc:
                action_error = exc
            refreshed = await self._instances.get_data_row(
                instance_id=inst.id, table_slug=table_slug, row_id=row_id
            )
            if refreshed is not None:
                out = {
                    "module_id": module_id,
                    "instance_id": inst.id,
                    **refreshed,
                    "rematerialize": remat,
                }
            if action_error is not None:
                raise action_error
        return out

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
        inst = await self._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        ok = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if not ok:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        if module_id == "mod_equipment" and table_slug == "catalogs":
            try:
                from prodavan.application.modules.equipment_catalog_opensearch import (
                    delete_equipment_catalog_index,
                )
                from prodavan.infrastructure.persistence.models.projects import ProjectRow

                project = await self._session.get(ProjectRow, project_id)
                if project is not None and project.company_id:
                    await delete_equipment_catalog_index(
                        row_id=row_id,
                        company_id=str(project.company_id),
                        cabinet_id=str(project.cabinet_id),
                        project_id=project_id,
                    )
            except Exception:
                logger.exception(
                    "equipment catalog OS delete failed project=%s row=%s",
                    project_id,
                    row_id,
                )
        await self._session.commit()
        remat: dict[str, Any] | None = None
        try:
            from prodavan.application.projects.workspace_sync_policy import (
                defer_or_schedule_project_sync,
            )

            notification = await defer_or_schedule_project_sync(
                self._session,
                project_id=project_id,
                source="project_module_instance",
            )
            remat = notification.rematerialize_alias()
        except Exception:
            logger.exception(
                "workspace sync after module row write failed project=%s module=%s",
                project_id,
                module_id,
            )
            remat = {"mode": "deferred", "error": "sync_failed"}
        return {"deleted": True, "row_id": row_id, "rematerialize": remat}

    async def _maybe_run_row_actions(
        self,
        *,
        project_id: str,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        previous_body: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        if not row_id:
            return
        from prodavan.application.modules.module_action_executor import ModuleActionExecutor

        await ModuleActionExecutor(self._session).maybe_auto_index_tabular(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            row_id=row_id,
            principal=principal,
            employee=employee,
            previous_body=previous_body,
            session_id=session_id,
        )
        await ModuleActionExecutor(self._session).maybe_auto_budget_sync(
            cabinet_id=cabinet_id,
            project_id=project_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
            session_id=session_id,
        )
