"""Platform / company module-instance data CRUD — scoped to the caller's copy only."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.module_instance_service import (
    OWNER_COMPANY,
    OWNER_PLATFORM,
    PLATFORM_OWNER_ID,
    ModuleInstanceService,
)
from prodavan.application.modules.module_row_helpers import (
    check_table_slug,
    ensure_row_body,
    merge_column_defaults,
    validate_row_with_columns,
)
from prodavan.domain.errors import AppError


class OwnerModuleDataService:
    """Mutate admin/company module instance rows without cascading to child forks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._instances = ModuleInstanceService(session)

    async def _ensure_instance(self, *, owner_kind: str, owner_id: str, module_id: str):
        if owner_kind == OWNER_PLATFORM:
            return await self._instances.ensure_platform_instance(module_id=module_id)
        if owner_kind == OWNER_COMPANY:
            # Prefer resolve+ensure: global grants return platform SoT without forking.
            sot = await self._instances.resolve_sot_instance(
                module_id=module_id,
                owner_kind=OWNER_COMPANY,
                owner_id=owner_id,
            )
            if sot is not None:
                return sot
            return await self._instances.ensure_company_instance(
                company_id=owner_id, module_id=module_id
            )
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"unsupported owner_kind: {owner_kind}",
        )

    async def list_data_rows(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
        table_slug: str,
    ) -> list[dict[str, Any]]:
        table_slug = check_table_slug(table_slug)
        inst = await self._ensure_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=module_id
        )
        # Persist fork so sibling list/create/delete hit the same instance_id.
        await self._session.commit()
        rows = await self._instances.list_data_rows(instance_id=inst.id, table_slug=table_slug)
        return [
            {
                "module_id": module_id,
                "instance_id": inst.id,
                "owner_kind": owner_kind,
                "owner_id": owner_id if owner_kind != OWNER_PLATFORM else PLATFORM_OWNER_ID,
                **row,
            }
            for row in rows
        ]

    async def create_data_row(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        inst = await self._ensure_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=module_id
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
            cabinet_id=None,
        )
        row = await self._instances.create_data_row(
            instance_id=inst.id,
            table_slug=table_slug,
            body=body,
            created_by=created_by,
        )
        await self._session.commit()
        return {"module_id": module_id, "instance_id": inst.id, **row}

    async def update_data_row(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        body = ensure_row_body(body)
        inst = await self._ensure_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=module_id
        )
        columns_body = await self._instances.resolve_columns_body(
            instance_id=inst.id, module_id=module_id
        )
        body = validate_row_with_columns(
            columns_body=columns_body,
            table_slug=table_slug,
            body=body,
            cabinet_id=None,
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
        owner_kind: str,
        owner_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
    ) -> dict[str, Any]:
        table_slug = check_table_slug(table_slug)
        inst = await self._ensure_instance(
            owner_kind=owner_kind, owner_id=owner_id, module_id=module_id
        )
        ok = await self._instances.delete_data_row(
            instance_id=inst.id, table_slug=table_slug, row_id=row_id
        )
        if not ok:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        await self._session.commit()
        return {
            "deleted": True,
            "row_id": row_id,
            "instance_id": inst.id,
            "owner_kind": owner_kind,
            "owner_id": owner_id if owner_kind != OWNER_PLATFORM else PLATFORM_OWNER_ID,
        }
