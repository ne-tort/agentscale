"""Cabinet bundle export / import (L06) — deep copy → new schema."""

from __future__ import annotations

import base64
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.cabinets.meta_service import CabinetMetaService
from prodavan.application.cabinets.packages_service import CabinetPackagesService
from prodavan.application.cabinets.rows_service import CabinetRowsService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.bundle_codec import pack_bundle, unpack_bundle
from prodavan.infrastructure.cabinets.sql import data_table_slug, qident, qualified
from prodavan.infrastructure.persistence.models.identity import EmployeeRow


class CabinetBundleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)
        self._instances = CabinetInstanceService(session)
        self._meta = CabinetMetaService(session)
        self._rows = CabinetRowsService(session)
        self._packages = CabinetPackagesService(session)

    async def export_zip(
        self,
        *,
        cabinet_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        include_data: bool = True,
    ) -> bytes:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=False
        )
        snapshot = await self._read_snapshot(inst.schema_name, include_data=include_data)
        pkg_list = await self._packages.load_enabled_artifacts(schema_name=inst.schema_name)
        package_files = {fname: raw for fname, raw in pkg_list}
        return pack_bundle(
            name=inst.name,
            exported_from_cabinet_id=inst.id,
            tables=snapshot["tables"],
            columns=snapshot["columns"],
            tabs=snapshot["tabs"],
            views=snapshot["views"],
            mcp_tools=snapshot["mcp_tools"],
            data_by_slug=snapshot["data_by_slug"],
            package_files=package_files,
        )

    async def export_base64(self, **kwargs: Any) -> dict:
        raw = await self.export_zip(**kwargs)
        return {
            "format": "cabinet.bundle",
            "format_version": 1,
            "zip_base64": base64.b64encode(raw).decode("ascii"),
            "size_bytes": len(raw),
        }

    async def import_zip(
        self,
        *,
        raw: bytes,
        company_id: str,
        employee: EmployeeRow,
        name_override: str | None = None,
        include_data: bool = True,
    ) -> dict:
        parsed = unpack_bundle(raw)
        manifest = parsed["manifest"]
        name = (name_override or manifest.get("name") or "Imported cabinet").strip()
        await CompanyQuotaService(self._session).assert_bundle_size(company_id, size_bytes=len(raw))
        await CompanyQuotaService(self._session).assert_can_create_cabinet(company_id)

        # New instance + new schema (Base seed tabs), then apply user tables/data
        created = await self._instances.create_from_base(
            name=name,
            company_id=company_id,
            employee=employee,
            base_template="base",
        )
        new_id = created["id"]

        # Platform admin / owner principal for subsequent writes
        owner_principal = Principal(sub=employee.keycloak_sub or employee.id, roles=frozenset())

        table_id_to_slug = {t["id"]: t["slug"] for t in parsed["tables"] if "id" in t and "slug" in t}
        cols_by_slug: dict[str, list[dict]] = {}
        for col in parsed["columns"]:
            slug = col.get("table_slug") or table_id_to_slug.get(col.get("table_id", ""), "")
            if not slug:
                continue
            cols_by_slug.setdefault(slug, []).append(
                {
                    "name": col["name"],
                    "type": col.get("col_type") or col.get("type"),
                    "required": bool(col.get("required")),
                    "unique": bool(col.get("unique") or col.get("unique_col")),
                    "ref_table_slug": col.get("ref_table_slug"),
                }
            )

        for table in parsed["tables"]:
            slug = table.get("slug")
            if not slug:
                continue
            cols = cols_by_slug.get(slug) or []
            if not cols:
                continue
            await self._meta.create_table(
                cabinet_id=new_id,
                slug=slug,
                label=str(table.get("label") or slug),
                storage_kind=str(table.get("storage_kind") or "physical"),
                columns=cols,
                principal=owner_principal,
                employee=employee,
            )

        if include_data:
            for slug, rows in parsed["data_by_slug"].items():
                for row in rows:
                    values = {k: v for k, v in row.items() if k not in {"id", "created_at"}}
                    await self._rows.upsert_row(
                        cabinet_id=new_id,
                        table_slug=slug,
                        values=values,
                        row_id=None,
                        principal=owner_principal,
                        employee=employee,
                    )

        meta_import = await self._meta.import_bundle_views_and_tabs(
            cabinet_id=new_id,
            views=parsed["views"],
            tabs=parsed["tabs"],
            principal=owner_principal,
            employee=employee,
        )

        packages_deployed = 0
        for _fname, raw_pkg in (parsed.get("package_files") or {}).items():
            await self._packages.deploy(
                cabinet_id=new_id,
                zip_bytes=raw_pkg,
                principal=owner_principal,
                employee=employee,
                replace_if_name=True,
            )
            packages_deployed += 1

        return {
            "cabinet": created,
            "imported_tables": len(parsed["tables"]),
            "imported_data_tables": len(parsed["data_by_slug"]) if include_data else 0,
            "views_imported": meta_import["views_imported"],
            "tabs_imported": meta_import["tabs_imported"],
            "mcp_packages_deployed": packages_deployed,
            "exported_from_cabinet_id": manifest.get("exported_from_cabinet_id"),
        }

    async def import_base64(
        self,
        *,
        zip_base64: str,
        company_id: str,
        employee: EmployeeRow,
        name_override: str | None = None,
    ) -> dict:
        try:
            raw = base64.b64decode(zip_base64, validate=True)
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="invalid zip_base64",
            ) from exc
        return await self.import_zip(
            raw=raw, company_id=company_id, employee=employee, name_override=name_override
        )

    async def _read_snapshot(self, schema_name: str, *, include_data: bool) -> dict[str, Any]:
        qschema = qident(schema_name)
        tables_q = await self._session.execute(
            text(
                f"""
                SELECT id, slug, label, storage_kind, status
                FROM {qschema}.meta_tables
                WHERE status = 'active'
                ORDER BY slug
                """
            )
        )
        tables = [
            {
                "id": r.id,
                "slug": r.slug,
                "label": r.label,
                "storage_kind": r.storage_kind,
                "status": r.status,
            }
            for r in tables_q.fetchall()
        ]

        cols_q = await self._session.execute(
            text(
                f"""
                SELECT c.id, c.table_id, t.slug AS table_slug, c.name, c.col_type,
                       c.required, c.unique_col, c.ref_table_slug
                FROM {qschema}.meta_columns c
                JOIN {qschema}.meta_tables t ON t.id = c.table_id AND t.status = 'active'
                ORDER BY t.slug, c.name
                """
            )
        )
        columns = [
            {
                "id": r.id,
                "table_id": r.table_id,
                "table_slug": r.table_slug,
                "name": r.name,
                "col_type": r.col_type,
                "required": r.required,
                "unique_col": r.unique_col,
                "ref_table_slug": r.ref_table_slug,
            }
            for r in cols_q.fetchall()
        ]

        views_q = await self._session.execute(
            text(
                f"""
                SELECT id, slug, table_slug, ui_json, version
                FROM {qschema}.meta_views
                ORDER BY slug
                """
            )
        )
        views = []
        for r in views_q.fetchall():
            ui = r.ui_json
            if isinstance(ui, str):
                try:
                    ui = json.loads(ui)
                except json.JSONDecodeError:
                    ui = {}
            views.append(
                {
                    "id": r.id,
                    "slug": r.slug,
                    "table_slug": r.table_slug,
                    "ui_json": ui,
                    "version": r.version,
                }
            )

        tabs_q = await self._session.execute(
            text(
                f"""
                SELECT id, title, tab_order, view_id, system_tab
                FROM {qschema}.meta_tabs
                ORDER BY tab_order
                """
            )
        )
        tabs = [
            {
                "id": r.id,
                "title": r.title,
                "order": r.tab_order,
                "view_id": r.view_id,
                "system": r.system_tab,
            }
            for r in tabs_q.fetchall()
        ]

        tools_q = await self._session.execute(
            text(
                f"""
                SELECT id, name, kind, config, status
                FROM {qschema}.meta_mcp_tools
                ORDER BY name
                """
            )
        )
        mcp_tools = []
        for r in tools_q.fetchall():
            cfg = r.config
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except json.JSONDecodeError:
                    cfg = {}
            mcp_tools.append(
                {"id": r.id, "name": r.name, "kind": r.kind, "config": cfg, "status": r.status}
            )

        data_by_slug: dict[str, list[dict]] = {}
        if include_data:
            for table in tables:
                slug = table["slug"]
                storage = table["storage_kind"]
                fq = qualified(schema_name, data_table_slug(slug))
                if storage == "physical":
                    col_names = [c["name"] for c in columns if c["table_slug"] == slug]
                    select_cols = ["id", "created_at"] + col_names
                    try:
                        rq = await self._session.execute(
                            text(f"SELECT {', '.join(qident(c) for c in select_cols)} FROM {fq}")
                        )
                    except Exception:
                        continue
                    rows = []
                    for row in rq.fetchall():
                        item: dict[str, Any] = {}
                        for i, name in enumerate(select_cols):
                            val = row[i]
                            if hasattr(val, "isoformat"):
                                val = val.isoformat()
                            item[name] = val
                        rows.append(item)
                elif storage == "json_document":
                    try:
                        rq = await self._session.execute(
                            text(f"SELECT id, created_at, document FROM {fq}")
                        )
                    except Exception:
                        continue
                    rows = []
                    for row in rq.fetchall():
                        doc = row.document
                        if isinstance(doc, str):
                            try:
                                doc = json.loads(doc)
                            except json.JSONDecodeError:
                                doc = {}
                        if not isinstance(doc, dict):
                            doc = {}
                        item = {
                            "id": row.id,
                            "created_at": row.created_at.isoformat() if row.created_at else None,
                            **doc,
                        }
                        rows.append(item)
                else:
                    continue
                if rows:
                    data_by_slug[slug] = rows

        return {
            "tables": tables,
            "columns": columns,
            "tabs": tabs,
            "views": views,
            "mcp_tools": mcp_tools,
            "data_by_slug": data_by_slug,
        }
