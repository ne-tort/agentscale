"""Meta catalog operations inside cabinet schema (L06)."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.cabinets import BASE_SYSTEM_TABS, COLUMN_TYPES, ColumnType, StorageKind
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

    async def get_table(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        tq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status, created_at
                FROM {qschema}.meta_tables
                WHERE slug = :slug AND status = 'active'
                """
            ),
            {"slug": table_slug},
        )
        table = tq.fetchone()
        if table is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Table not found")
        cq = await self._session.execute(
            text(
                f"""
                SELECT name, col_type, required, unique_col, ref_table_slug
                FROM {qschema}.meta_columns
                WHERE table_id = :tid
                ORDER BY name
                """
            ),
            {"tid": table.id},
        )
        columns = [
            {
                "name": c.name,
                "type": c.col_type,
                "required": bool(c.required),
                "unique": bool(c.unique_col),
                "ref_table_slug": c.ref_table_slug,
            }
            for c in cq.fetchall()
        ]
        return {
            "id": table.id,
            "slug": table.slug,
            "label": table.label,
            "storage_kind": table.storage_kind,
            "status": table.status,
            "created_at": table.created_at.isoformat() if table.created_at else None,
            "columns": columns,
        }

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
                SELECT t.id, t.title, t.tab_order, t.view_id, t.system_tab, t.created_at,
                       v.slug AS view_slug, v.table_slug
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
                "table_slug": r.table_slug,
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

    async def import_bundle_views_and_tabs(
        self,
        *,
        cabinet_id: str,
        views: list[dict],
        tabs: list[dict],
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        """Apply non-system views/tabs from cabinet.bundle import (L06)."""
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        system_slugs = {slug for _, _, slug in BASE_SYSTEM_TABS}
        qschema = qident(inst.schema_name)
        view_id_by_slug: dict[str, str] = {}

        for view in views:
            slug = str(view.get("slug") or "").strip()
            if not slug or slug in system_slugs:
                continue
            ui = view.get("ui_json") or {}
            if not isinstance(ui, dict):
                ui = {}
            view_id = f"view_{uuid.uuid4().hex[:12]}"
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_views (id, slug, table_slug, ui_json, version)
                    VALUES (:id, :slug, :table_slug, CAST(:ui AS jsonb), :ver)
                    ON CONFLICT (slug) DO UPDATE SET
                        table_slug = EXCLUDED.table_slug,
                        ui_json = EXCLUDED.ui_json,
                        version = EXCLUDED.version
                    """
                ),
                {
                    "id": view_id,
                    "slug": slug,
                    "table_slug": view.get("table_slug"),
                    "ui": json.dumps(ui, ensure_ascii=False),
                    "ver": int(view.get("version") or 1),
                },
            )
            id_q = await self._session.execute(
                text(f"SELECT id FROM {qschema}.meta_views WHERE slug = :slug"),
                {"slug": slug},
            )
            resolved_id = id_q.scalar_one()
            view_id_by_slug[slug] = str(resolved_id)

        exported_view_id_to_slug = {
            str(v.get("id")): str(v.get("slug"))
            for v in views
            if v.get("id") and v.get("slug")
        }
        tabs_added = 0
        for tab in sorted(tabs, key=lambda t: int(t.get("order") or 0)):
            if tab.get("system"):
                continue
            old_view_id = str(tab.get("view_id") or "")
            view_slug = exported_view_id_to_slug.get(old_view_id, "")
            if not view_slug or view_slug in system_slugs:
                continue
            new_view_id = view_id_by_slug.get(view_slug)
            if not new_view_id:
                continue
            tab_id = f"tab_{uuid.uuid4().hex[:12]}"
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_tabs (id, title, tab_order, view_id, system_tab)
                    VALUES (:id, :title, :ord, :vid, false)
                    """
                ),
                {
                    "id": tab_id,
                    "title": str(tab.get("title") or view_slug),
                    "ord": int(tab.get("order") or 0),
                    "vid": new_view_id,
                },
            )
            tabs_added += 1

        await self._session.commit()
        return {"views_imported": len(view_id_by_slug), "tabs_imported": tabs_added}

