"""Execute declarative module actions from meta slug `actions`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.cabinet_module_service import CabinetModuleService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import ModuleMetaDocumentRow


class ModuleActionExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._modules = CabinetModuleService(session)

    async def invoke(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        action_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        action = await self._load_action(module_id=module_id, action_id=action_id)
        if action.get("enabled") is False:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="action is disabled",
            )
        kind = str(action.get("kind") or "")
        params = action.get("params") if isinstance(action.get("params"), dict) else {}

        if kind == "data.create_row":
            table_slug = params.get("table_slug")
            if not isinstance(table_slug, str) or not table_slug:
                raise AppError(
                    code="META_VALIDATION",
                    title="Meta validation error",
                    status=422,
                    detail="data.create_row requires params.table_slug",
                )
            defaults = params.get("defaults")
            body = dict(defaults) if isinstance(defaults, dict) else {}
            row = await self._modules.create_data_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                body=body,
                principal=principal,
                employee=employee,
            )
            return {"kind": kind, "row": row}

        if kind == "data.delete_row":
            table_slug = params.get("table_slug")
            if not isinstance(table_slug, str) or not table_slug:
                raise AppError(
                    code="META_VALIDATION",
                    title="Meta validation error",
                    status=422,
                    detail="data.delete_row requires params.table_slug",
                )
            if not row_id:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="row_id required for data.delete_row",
                )
            await self._modules.delete_data_row(
                cabinet_id=cabinet_id,
                module_id=module_id,
                table_slug=table_slug,
                row_id=row_id,
                principal=principal,
                employee=employee,
            )
            return {"kind": kind, "deleted_row_id": row_id}

        raise AppError(
            code="NOT_IMPLEMENTED",
            title="Not Implemented",
            status=501,
            detail=f"action kind not supported: {kind}",
        )

    async def _load_action(self, *, module_id: str, action_id: str) -> dict[str, Any]:
        q = await self._session.execute(
            select(ModuleMetaDocumentRow.body).where(
                ModuleMetaDocumentRow.module_id == module_id,
                ModuleMetaDocumentRow.slug == "actions",
            )
        )
        body = q.scalar_one_or_none()
        if not isinstance(body, list):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="actions not found")
        for item in body:
            if isinstance(item, dict) and str(item.get("id")) == action_id:
                return item
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="action not found")
