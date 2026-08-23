"""Meta catalog operations inside cabinet schema (L06)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.cabinets import COLUMN_TYPES, ColumnType, StorageKind
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import data_table_slug, qident, qualified
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_PG_TYPE: dict[str, str] = {
    ColumnType.TEXT: "TEXT",
    ColumnType.NUMBER: "NUMERIC",
    ColumnType.BOOL: "BOOLEAN",
    ColumnType.DATETIME: "TIMESTAMPTZ",
    ColumnType.JSON: "JSONB",
    ColumnType.ENUM: "TEXT",
    ColumnType.REF: "TEXT",
    ColumnType.FILE_REF: "TEXT",
}


class CabinetMetaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def list_tables(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status, created_at
                FROM {qschema}.meta_tables
                WHERE status = 'active'
                ORDER BY slug
                """
            )
        )
        return [
            {
                "id": r.id,
                "slug": r.slug,
                "label": r.label,
                "storage_kind": r.storage_kind,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in q.fetchall()
        ]

    async def list_tabs(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        q = await self._session.execute(
            text(
                f"""
                SELECT t.id, t.title, t.tab_order, t.view_id, t.system_tab, t.created_at, v.slug AS view_slug
                FROM {qschema}.meta_tabs t
                LEFT JOIN {qschema}.meta_views v ON v.id = t.view_id
                ORDER BY t.tab_order
                """
            )
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "order": r.tab_order,
                "view_id": r.view_id,
                "view_slug": r.view_slug,
                "system": r.system_tab,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in q.fetchall()
        ]

    async def create_table(
        self,
        *,
        cabinet_id: str,
        slug: str,
        label: str,
        storage_kind: str,
        columns: list[dict],
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        if storage_kind not in {StorageKind.PHYSICAL, StorageKind.JSON_DOCUMENT}:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad storage_kind")
        if not slug.replace("_", "").isalnum() or not slug.islower():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad slug")
        if not columns:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="columns required")

        qschema = qident(inst.schema_name)
        table_id = f"tbl_{uuid.uuid4().hex[:12]}"

        await self._session.execute(
            text(
                f"""
                INSERT INTO {qschema}.meta_tables (id, slug, label, storage_kind)
                VALUES (:id, :slug, :label, :sk)
                """
            ),
            {"id": table_id, "slug": slug, "label": label.strip(), "sk": storage_kind},
        )

        ddl_cols = ['"id" TEXT PRIMARY KEY', '"created_at" TIMESTAMPTZ NOT NULL DEFAULT now()']
        for col in columns:
            name = col.get("name", "")
            col_type = col.get("type", "")
            if not name.replace("_", "").isalnum():
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad column name")
            if col_type not in COLUMN_TYPES:
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad column type")
            col_id = f"col_{uuid.uuid4().hex[:12]}"
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_columns
                    (id, table_id, name, col_type, required, unique_col, ref_table_slug)
                    VALUES (:id, :tid, :name, :ctype, :req, :uniq, :ref)
                    """
                ),
                {
                    "id": col_id,
                    "tid": table_id,
                    "name": name,
                    "ctype": col_type,
                    "req": bool(col.get("required")),
                    "uniq": bool(col.get("unique")),
                    "ref": col.get("ref_table_slug"),
                },
            )
            pg = _PG_TYPE.get(col_type, "TEXT")
            not_null = " NOT NULL" if col.get("required") else ""
            ddl_cols.append(f"{qident(name)} {pg}{not_null}")

        if storage_kind == StorageKind.PHYSICAL:
            fq = qualified(inst.schema_name, data_table_slug(slug))
            await self._session.execute(text(f"CREATE TABLE IF NOT EXISTS {fq} ({', '.join(ddl_cols)})"))

        await self._session.commit()
        return {"id": table_id, "slug": slug, "label": label.strip(), "storage_kind": storage_kind}
