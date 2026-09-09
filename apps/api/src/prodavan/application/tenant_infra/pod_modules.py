"""Pod-facing module data access under Bridge scopes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_binding_service import ModuleBindingService
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    validate_row_with_columns,
)
from prodavan.application.pod_identity.bridge import PodBridgeClaims, module_rows_scope
from prodavan.application.projects.project_runtime_module_service import ProjectRuntimeModuleService
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.projects import ProjectRow


class PodModuleDataService:
    """Rows CRUD for Project Pods — Bridge JWT + module:{id}:rows scope only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runtime = ProjectRuntimeModuleService(session)
        self._instances = ModuleInstanceService(session)

    async def _require_project_row(self, project_id: str, bridge: PodBridgeClaims) -> ProjectRow:
        bridge.require_project(project_id)
        row = await self._session.get(ProjectRow, project_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        if row.company_id != bridge.company_id or row.cabinet_id != bridge.cabinet_id:
            raise AppError(
                code="FORBIDDEN",
                title="Forbidden",
                status=403,
                detail="pod bridge tenancy mismatch",
            )
        return row

    def _require_module_rows(self, bridge: PodBridgeClaims, module_id: str) -> None:
        bridge.require_scope(module_rows_scope(module_id))

    async def list_data_rows(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
    ) -> list[dict[str, Any]]:
        await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=False
        )
        rows = await self._instances.list_data_rows(instance_id=inst.id, table_slug=table_slug)
        return [
            {"module_id": module_id, "instance_id": inst.id, **row}
            for row in rows
        ]

    async def create_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
    ) -> dict[str, Any]:
        project = await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
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
        row = await self._instances.create_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            body=body,
            created_by=f"pod:{bridge.pod_id}",
        )
        await self._session.commit()
        return {"module_id": module_id, "instance_id": inst.id, **row}

    async def update_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
    ) -> dict[str, Any]:
        project = await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
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
        )
        await self._session.commit()
        return {"module_id": module_id, "instance_id": inst.id, **row}

    async def delete_data_row(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
    ) -> dict[str, Any]:
        await self._require_project_row(project_id, bridge)
        self._require_module_rows(bridge, module_id)
        table_slug = check_table_slug(table_slug)
        inst = await self._runtime._sot_for_project(
            project_id=project_id, module_id=module_id, write=True
        )
        deleted = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        await self._session.commit()
        if not deleted:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        return {"module_id": module_id, "deleted": True, "row_id": row_id}

    async def list_bound_modules(
        self, *, bridge: PodBridgeClaims, project_id: str
    ) -> list[str]:
        await self._require_project_row(project_id, bridge)
        return await ModuleBindingService(self._session).list_module_ids_for_project(project_id)
