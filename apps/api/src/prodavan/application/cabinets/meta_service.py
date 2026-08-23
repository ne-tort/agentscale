"""Meta catalog operations inside cabinet schema (L06)."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.cabinets.audit_service import CabinetAuditService
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

_PROTECTED_COLUMNS = frozenset({"id", "created_at"})


def _system_view_slugs() -> set[str]:
    return {slug for _, _, slug in BASE_SYSTEM_TABS}


def _parse_column_def(column: dict) -> tuple[str, str]:
    name = str(column.get("name") or "").strip()
    col_type = str(column.get("type") or "")
    if not name.replace("_", "").isalnum():
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad column name")
    if col_type not in COLUMN_TYPES:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad column type")
    return name, col_type


class CabinetMetaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._audit = CabinetAuditService(session)

    async def _emit_audit(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        event_type: str,
        detail: dict,
    ) -> None:
        try:
            await self._audit.record(
                cabinet_id=cabinet_id,
                event_type=event_type,
                tool_name=None,
                principal=principal,
                detail=detail,
            )
        except Exception:
            pass

    async def list_tables(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        include_archived: bool = False,
    ) -> list[dict]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        qschema = qident(inst.schema_name)
        status_clause = "" if include_archived else "AND status = 'active'"
        q = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status, created_at
                FROM {qschema}.meta_tables
                WHERE 1=1 {status_clause}
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
        dup = await self._session.execute(
            text(f"SELECT 1 FROM {qschema}.meta_tables WHERE slug = :slug AND status = 'active'"),
            {"slug": slug},
        )
        if dup.scalar_one_or_none() is not None:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="table slug already exists")

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
            name, col_type = _parse_column_def(col)
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
        elif storage_kind == StorageKind.JSON_DOCUMENT:
            fq = qualified(inst.schema_name, data_table_slug(slug))
            await self._session.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {fq} (
                        "id" TEXT PRIMARY KEY,
                        "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
                        "document" JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
            )

        await self._session.commit()
        result = {"id": table_id, "slug": slug, "label": label.strip(), "storage_kind": storage_kind}
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.table.create",
            detail={"slug": slug, "storage_kind": storage_kind, "columns": len(columns)},
        )
        return result

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

    async def _require_table(
        self,
        *,
        schema_name: str,
        table_slug: str,
    ):
        qschema = qident(schema_name)
        tq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind
                FROM {qschema}.meta_tables
                WHERE slug = :slug AND status = 'active'
                """
            ),
            {"slug": table_slug},
        )
        table = tq.fetchone()
        if table is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Table not found")
        return table

    async def _get_table_any(
        self,
        *,
        schema_name: str,
        table_slug: str,
    ):
        qschema = qident(schema_name)
        tq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status
                FROM {qschema}.meta_tables
                WHERE slug = :slug
                """
            ),
            {"slug": table_slug},
        )
        table = tq.fetchone()
        if table is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Table not found")
        return table

    async def add_column(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        column: dict,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        table = await self._require_table(schema_name=inst.schema_name, table_slug=table_slug)
        name, col_type = _parse_column_def(column)

        qschema = qident(inst.schema_name)
        cq = await self._session.execute(
            text(
                f"""
                SELECT 1 FROM {qschema}.meta_columns
                WHERE table_id = :tid AND name = :name
                """
            ),
            {"tid": table.id, "name": name},
        )
        if cq.scalar_one_or_none() is not None:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="column already exists")

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
                "tid": table.id,
                "name": name,
                "ctype": col_type,
                "req": bool(column.get("required")),
                "uniq": bool(column.get("unique")),
                "ref": column.get("ref_table_slug"),
            },
        )

        if table.storage_kind == StorageKind.PHYSICAL:
            pg = _PG_TYPE.get(col_type, "TEXT")
            not_null = " NOT NULL" if column.get("required") else ""
            fq = qualified(inst.schema_name, data_table_slug(table_slug))
            await self._session.execute(
                text(f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {qident(name)} {pg}{not_null}")
            )

        await self._session.commit()
        col_result = {
            "name": name,
            "type": col_type,
            "required": bool(column.get("required")),
            "unique": bool(column.get("unique")),
            "ref_table_slug": column.get("ref_table_slug"),
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.column.add",
            detail={"table_slug": table_slug, "column": name, "type": col_type},
        )
        return col_result

    async def delete_column(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        column_name: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        name = column_name.strip()
        if name in _PROTECTED_COLUMNS:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="protected column")

        table = await self._require_table(schema_name=inst.schema_name, table_slug=table_slug)
        qschema = qident(inst.schema_name)
        cq = await self._session.execute(
            text(
                f"""
                SELECT id FROM {qschema}.meta_columns
                WHERE table_id = :tid AND name = :name
                """
            ),
            {"tid": table.id, "name": name},
        )
        col_id = cq.scalar_one_or_none()
        if col_id is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="column not found")

        await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_columns WHERE id = :id"),
            {"id": str(col_id)},
        )
        if table.storage_kind == StorageKind.PHYSICAL:
            fq = qualified(inst.schema_name, data_table_slug(table_slug))
            await self._session.execute(
                text(f"ALTER TABLE {fq} DROP COLUMN IF EXISTS {qident(name)}")
            )
        await self._session.commit()
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.column.delete",
            detail={"table_slug": table_slug, "column": name},
        )

    async def list_views(
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
                SELECT id, slug, table_slug, ui_json, version, created_at
                FROM {qschema}.meta_views
                ORDER BY slug
                """
            )
        )
        out: list[dict] = []
        for r in q.fetchall():
            ui = r.ui_json
            if isinstance(ui, str):
                try:
                    ui = json.loads(ui)
                except json.JSONDecodeError:
                    ui = {}
            out.append(
                {
                    "id": r.id,
                    "slug": r.slug,
                    "table_slug": r.table_slug,
                    "ui_json": ui if isinstance(ui, dict) else {},
                    "version": r.version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return out

    async def create_view(
        self,
        *,
        cabinet_id: str,
        slug: str,
        table_slug: str | None,
        ui_json: dict,
        version: int,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        system_slugs = _system_view_slugs()
        if not slug.replace("_", "").isalnum() or not slug.islower():
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad slug")
        if slug in system_slugs:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="reserved view slug")

        qschema = qident(inst.schema_name)
        if table_slug:
            await self._require_table(schema_name=inst.schema_name, table_slug=table_slug)

        view_id = f"view_{uuid.uuid4().hex[:12]}"
        try:
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO {qschema}.meta_views (id, slug, table_slug, ui_json, version)
                    VALUES (:id, :slug, :table_slug, CAST(:ui AS jsonb), :ver)
                    """
                ),
                {
                    "id": view_id,
                    "slug": slug,
                    "table_slug": table_slug,
                    "ui": json.dumps(ui_json, ensure_ascii=False),
                    "ver": version,
                },
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise AppError(code="CONFLICT", title="Conflict", status=409, detail="view slug exists") from exc
            raise

        await self._session.commit()
        view_result = {
            "id": view_id,
            "slug": slug,
            "table_slug": table_slug,
            "ui_json": ui_json,
            "version": version,
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.view.create",
            detail={"slug": slug, "table_slug": table_slug},
        )
        return view_result

    async def _require_view_row(self, *, schema_name: str, view_slug: str):
        qschema = qident(schema_name)
        if view_slug in _system_view_slugs():
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="reserved view slug")
        vq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, table_slug, ui_json, version
                FROM {qschema}.meta_views
                WHERE slug = :slug
                """
            ),
            {"slug": view_slug},
        )
        view = vq.fetchone()
        if view is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="view not found")
        return view

    async def update_view(
        self,
        *,
        cabinet_id: str,
        view_slug: str,
        patch: dict,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        view = await self._require_view_row(schema_name=inst.schema_name, view_slug=view_slug)
        qschema = qident(inst.schema_name)

        new_table_slug = view.table_slug
        if "table_slug" in patch:
            table_slug = patch["table_slug"]
            if table_slug:
                await self._require_table(schema_name=inst.schema_name, table_slug=str(table_slug))
            new_table_slug = table_slug

        new_ui = view.ui_json
        if isinstance(new_ui, str):
            try:
                new_ui = json.loads(new_ui)
            except json.JSONDecodeError:
                new_ui = {}
        if "ui_json" in patch and patch["ui_json"] is not None:
            new_ui = patch["ui_json"]

        new_version = patch.get("version", view.version)

        await self._session.execute(
            text(
                f"""
                UPDATE {qschema}.meta_views
                SET table_slug = :table_slug, ui_json = CAST(:ui AS jsonb), version = :ver
                WHERE id = :id
                """
            ),
            {
                "id": view.id,
                "table_slug": new_table_slug,
                "ui": json.dumps(new_ui if isinstance(new_ui, dict) else {}, ensure_ascii=False),
                "ver": new_version,
            },
        )
        await self._session.commit()
        view_result = {
            "id": view.id,
            "slug": view.slug,
            "table_slug": new_table_slug,
            "ui_json": new_ui if isinstance(new_ui, dict) else {},
            "version": new_version,
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.view.update",
            detail={"slug": view.slug, "patch_keys": sorted(patch.keys())},
        )
        return view_result

    async def delete_view(
        self,
        *,
        cabinet_id: str,
        view_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        view = await self._require_view_row(schema_name=inst.schema_name, view_slug=view_slug)
        qschema = qident(inst.schema_name)
        refs = await self._session.execute(
            text(f"SELECT 1 FROM {qschema}.meta_tabs WHERE view_id = :vid LIMIT 1"),
            {"vid": view.id},
        )
        if refs.scalar_one_or_none() is not None:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="view has tabs")

        await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_views WHERE id = :id"),
            {"id": view.id},
        )
        await self._session.commit()
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.view.delete",
            detail={"slug": view_slug},
        )

    async def create_tab(
        self,
        *,
        cabinet_id: str,
        title: str,
        order: int,
        view_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        qschema = qident(inst.schema_name)
        vq = await self._session.execute(
            text(f"SELECT id FROM {qschema}.meta_views WHERE slug = :slug"),
            {"slug": view_slug},
        )
        view_id = vq.scalar_one_or_none()
        if view_id is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="view not found")

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
                "title": title.strip(),
                "ord": order,
                "vid": str(view_id),
            },
        )
        await self._session.commit()
        tab_result = {
            "id": tab_id,
            "title": title.strip(),
            "order": order,
            "view_slug": view_slug,
            "system": False,
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.tab.create",
            detail={"title": title.strip(), "view_slug": view_slug},
        )
        return tab_result

    async def _require_tab_row(self, *, schema_name: str, tab_id: str):
        qschema = qident(schema_name)
        tq = await self._session.execute(
            text(
                f"""
                SELECT t.id, t.title, t.tab_order, t.view_id, t.system_tab, v.slug AS view_slug
                FROM {qschema}.meta_tabs t
                LEFT JOIN {qschema}.meta_views v ON v.id = t.view_id
                WHERE t.id = :id
                """
            ),
            {"id": tab_id},
        )
        tab = tq.fetchone()
        if tab is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="tab not found")
        if tab.system_tab:
            raise AppError(code="CONFLICT", title="Conflict", status=409, detail="system tab")
        return tab

    async def update_tab(
        self,
        *,
        cabinet_id: str,
        tab_id: str,
        title: str | None = None,
        order: int | None = None,
        view_slug: str | None = None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        tab = await self._require_tab_row(schema_name=inst.schema_name, tab_id=tab_id)
        qschema = qident(inst.schema_name)

        new_title = title.strip() if title is not None else tab.title
        new_order = order if order is not None else tab.tab_order
        new_view_id = tab.view_id
        new_view_slug = tab.view_slug

        if view_slug is not None:
            vq = await self._session.execute(
                text(f"SELECT id, slug FROM {qschema}.meta_views WHERE slug = :slug"),
                {"slug": view_slug},
            )
            view = vq.fetchone()
            if view is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="view not found")
            new_view_id = view.id
            new_view_slug = view.slug

        await self._session.execute(
            text(
                f"""
                UPDATE {qschema}.meta_tabs
                SET title = :title, tab_order = :ord, view_id = :vid
                WHERE id = :id
                """
            ),
            {"id": tab_id, "title": new_title, "ord": new_order, "vid": new_view_id},
        )
        await self._session.commit()
        tab_result = {
            "id": tab_id,
            "title": new_title,
            "order": new_order,
            "view_slug": new_view_slug,
            "system": False,
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.tab.update",
            detail={"tab_id": tab_id},
        )
        return tab_result

    async def delete_tab(
        self,
        *,
        cabinet_id: str,
        tab_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        await self._require_tab_row(schema_name=inst.schema_name, tab_id=tab_id)
        qschema = qident(inst.schema_name)
        await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_tabs WHERE id = :id"),
            {"id": tab_id},
        )
        await self._session.commit()
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.tab.delete",
            detail={"tab_id": tab_id},
        )

    async def update_table(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        patch: dict,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        table = await self._require_table(schema_name=inst.schema_name, table_slug=table_slug)
        qschema = qident(inst.schema_name)

        if "label" not in patch:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="empty patch")

        label = str(patch["label"]).strip()
        if not label:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="label required")

        await self._session.execute(
            text(f"UPDATE {qschema}.meta_tables SET label = :label WHERE id = :id"),
            {"label": label, "id": table.id},
        )
        await self._session.commit()
        tq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status
                FROM {qschema}.meta_tables
                WHERE id = :id
                """
            ),
            {"id": table.id},
        )
        row = tq.fetchone()
        table_result = {
            "id": row.id,
            "slug": row.slug,
            "label": row.label,
            "storage_kind": row.storage_kind,
            "status": row.status,
        }
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.table.update",
            detail={"slug": table_slug, "label": label},
        )
        return table_result

    async def archive_table(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        table = await self._require_table(schema_name=inst.schema_name, table_slug=table_slug)
        qschema = qident(inst.schema_name)

        refs = await self._session.execute(
            text(
                f"""
                SELECT slug FROM {qschema}.meta_views
                WHERE table_slug = :slug
                LIMIT 1
                """
            ),
            {"slug": table_slug},
        )
        ref_slug = refs.scalar_one_or_none()
        if ref_slug is not None:
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail=f"view '{ref_slug}' references table",
            )

        await self._session.execute(
            text(f"UPDATE {qschema}.meta_tables SET status = 'archived' WHERE id = :id"),
            {"id": table.id},
        )
        await self._session.commit()
        archive_result = {"slug": table_slug, "status": "archived"}
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.table.archive",
            detail={"slug": table_slug},
        )
        return archive_result

    async def delete_table(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=True
        )
        table = await self._get_table_any(schema_name=inst.schema_name, table_slug=table_slug)
        if table.status != "archived":
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail="table must be archived before delete",
            )

        qschema = qident(inst.schema_name)
        refs = await self._session.execute(
            text(
                f"""
                SELECT slug FROM {qschema}.meta_views
                WHERE table_slug = :slug
                LIMIT 1
                """
            ),
            {"slug": table_slug},
        )
        ref_slug = refs.scalar_one_or_none()
        if ref_slug is not None:
            raise AppError(
                code="CONFLICT",
                title="Conflict",
                status=409,
                detail=f"view '{ref_slug}' references table",
            )

        fq = qualified(inst.schema_name, data_table_slug(table_slug))
        await self._session.execute(text(f"DROP TABLE IF EXISTS {fq}"))
        await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_columns WHERE table_id = :tid"),
            {"tid": table.id},
        )
        await self._session.execute(
            text(f"DELETE FROM {qschema}.meta_tables WHERE id = :id"),
            {"id": table.id},
        )
        await self._session.commit()
        delete_result = {"slug": table_slug, "deleted": True}
        await self._emit_audit(
            cabinet_id=cabinet_id,
            principal=principal,
            event_type="meta.table.delete",
            detail={"slug": table_slug},
        )
        return delete_result

