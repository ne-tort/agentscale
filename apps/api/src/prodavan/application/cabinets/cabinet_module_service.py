"""Cabinet runtime — bound modules, shared meta templates, per-cabinet data."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_row_validator import (
    columns_for_table,
    validate_row_body,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import qident
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleCabinetBindingRow,
    ModuleRow,
)

_TABLE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _check_table_slug(table_slug: str) -> str:
    table_slug = table_slug.strip().lower()
    if not _TABLE_SLUG_RE.match(table_slug):
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad table_slug")
    return table_slug


def _ensure_row_body(body: Any) -> dict:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="body must be valid JSON",
            ) from exc
    if not isinstance(body, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be a JSON object",
        )
    try:
        json.dumps(body)
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body is not JSON-serializable",
        ) from exc
    return body


class CabinetModuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._meta = ModuleMetaDocumentService(session)

    async def list_modules(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        q = await self._session.execute(
            select(ModuleCabinetBindingRow, ModuleRow)
            .join(ModuleRow, ModuleRow.id == ModuleCabinetBindingRow.module_id)
            .where(ModuleCabinetBindingRow.cabinet_id == cabinet_id)
            .order_by(ModuleRow.name)
        )
        return [
            {
                "id": mod.id,
                "name": mod.name,
                "status": mod.status,
            }
            for _bind, mod in q.all()
        ]

    async def get_meta_document(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        await self._require_module_binding(cabinet_id=cabinet_id, module_id=module_id)
        await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        doc = await self._meta.get_document(module_id=module_id, slug=slug)
        return {"module_id": module_id, **doc}

    async def list_data_rows(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        table_slug = _check_table_slug(table_slug)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        await self._require_installed(inst.schema_name, module_id=module_id)
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT row_id, body, created_by, created_at, updated_at
                FROM {qschema}.module_data_rows
                WHERE module_id = :module_id AND table_slug = :table_slug
                ORDER BY updated_at DESC
                """
            ),
            {"module_id": module_id, "table_slug": table_slug},
        )
        return [
            {
                "module_id": module_id,
                "table_slug": table_slug,
                "row_id": r.row_id,
                "body": r.body,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in q.fetchall()
        ]

    async def create_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        table_slug = _check_table_slug(table_slug)
        body = _ensure_row_body(body)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_installed(inst.schema_name, module_id=module_id)
        body = await self._merge_column_defaults(
            module_id=module_id, table_slug=table_slug, body=body
        )
        body = await self._validate_row(
            module_id=module_id, table_slug=table_slug, body=body
        )
        row_id = f"row_{uuid.uuid4().hex[:12]}"
        created_by = employee.id if employee is not None else principal.sub
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.module_data_rows
                    (module_id, table_slug, row_id, body, created_by, created_at, updated_at)
                VALUES
                    (:module_id, :table_slug, :row_id, CAST(:body AS jsonb), :created_by, now(), now())
                """
            ),
            {
                "module_id": module_id,
                "table_slug": table_slug,
                "row_id": row_id,
                "body": json.dumps(body),
                "created_by": created_by,
            },
        )
        await self._session.commit()
        rows = await self.list_data_rows(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
        )
        for row in rows:
            if row["row_id"] == row_id:
                return row
        raise AppError(code="INTERNAL", title="Internal Error", status=500, detail="row not found after insert")

    async def update_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        body: Any,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        table_slug = _check_table_slug(table_slug)
        body = _ensure_row_body(body)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_installed(inst.schema_name, module_id=module_id)
        body = await self._validate_row(
            module_id=module_id, table_slug=table_slug, body=body
        )
        qschema = qident(inst.schema_name)
        result = await self._session.execute(
            text(
                f"""
                UPDATE {qschema}.module_data_rows
                SET body = CAST(:body AS jsonb), updated_at = now()
                WHERE module_id = :module_id AND table_slug = :table_slug AND row_id = :row_id
                """
            ),
            {
                "module_id": module_id,
                "table_slug": table_slug,
                "row_id": row_id,
                "body": json.dumps(body),
            },
        )
        if result.rowcount == 0:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        await self._session.commit()
        rows = await self.list_data_rows(
            cabinet_id=cabinet_id,
            module_id=module_id,
            table_slug=table_slug,
            principal=principal,
            employee=employee,
        )
        for row in rows:
            if row["row_id"] == row_id:
                return row
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")

    async def delete_data_row(
        self,
        *,
        cabinet_id: str,
        module_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        table_slug = _check_table_slug(table_slug)
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_installed(inst.schema_name, module_id=module_id)
        qschema = qident(inst.schema_name)
        result = await self._session.execute(
            text(
                f"""
                DELETE FROM {qschema}.module_data_rows
                WHERE module_id = :module_id AND table_slug = :table_slug AND row_id = :row_id
                """
            ),
            {"module_id": module_id, "table_slug": table_slug, "row_id": row_id},
        )
        if result.rowcount == 0:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="row not found")
        await self._session.commit()

    async def _merge_column_defaults(
        self, *, module_id: str, table_slug: str, body: dict
    ) -> dict:
        """Fill missing keys from columns[].default in module meta (request wins)."""
        try:
            doc = await self._meta.get_document(module_id=module_id, slug="columns")
        except AppError:
            return body
        raw = doc.get("body")
        if not isinstance(raw, list):
            return body
        merged = dict(body)
        for col in raw:
            if not isinstance(col, dict):
                continue
            if col.get("table_slug") != table_slug:
                continue
            name = col.get("name")
            if not isinstance(name, str) or not name or name in merged:
                continue
            if "default" in col:
                merged[name] = col["default"]
        return merged

    async def _validate_row(self, *, module_id: str, table_slug: str, body: dict) -> dict:
        try:
            doc = await self._meta.get_document(module_id=module_id, slug="columns")
        except AppError:
            return body
        raw = doc.get("body")
        columns = columns_for_table(raw, table_slug)
        if not columns:
            return body
        return validate_row_body(body, columns)

    async def _require_module_binding(self, *, cabinet_id: str, module_id: str) -> None:
        q = await self._session.execute(
            select(ModuleCabinetBindingRow.id).where(
                ModuleCabinetBindingRow.cabinet_id == cabinet_id,
                ModuleCabinetBindingRow.module_id == module_id,
            )
        )
        if q.scalar_one_or_none() is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="module is not bound to cabinet",
            )

    async def _require_installed(self, schema_name: str, *, module_id: str) -> None:
        qschema = qident(schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT 1 FROM {qschema}.module_installations
                WHERE module_id = :module_id
                """
            ),
            {"module_id": module_id},
        )
        if q.scalar_one_or_none() is None:
            raise AppError(
                code="NOT_FOUND",
                title="Not Found",
                status=404,
                detail="module is not installed in cabinet",
            )
