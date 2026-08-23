"""Cabinet data plane — controlled rows query/upsert/delete (L06)."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.cabinets.access import CabinetAccessService
from prodavan.domain.cabinets import ColumnType, StorageKind
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.cabinets.sql import data_table_slug, qident, qualified
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_MAX_PAGE = 200


class _TableMeta:
    __slots__ = ("slug", "storage_kind", "columns")

    def __init__(self, slug: str, storage_kind: str, columns: list[dict]) -> None:
        self.slug = slug
        self.storage_kind = storage_kind
        self.columns = columns


class CabinetRowsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._access = CabinetAccessService(session)

    async def query_rows(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        inst, meta = await self._load_table(cabinet_id, table_slug, principal, employee, write=False)
        if limit < 1 or limit > _MAX_PAGE:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad limit")
        if offset < 0:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad offset")

        if meta.storage_kind == StorageKind.JSON_DOCUMENT:
            rows = await self._query_json_rows(inst.schema_name, meta, limit, offset)
        else:
            rows = await self._query_physical_rows(inst.schema_name, meta, limit, offset)
        return {"table_slug": table_slug, "rows": rows, "limit": limit, "offset": offset}

    async def upsert_row(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        values: dict[str, Any],
        row_id: str | None,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        inst, meta = await self._load_table(cabinet_id, table_slug, principal, employee, write=True)
        allowed = {c["name"]: c for c in meta.columns}
        unknown = set(values) - set(allowed)
        if unknown:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"unknown fields: {', '.join(sorted(unknown))}",
            )

        normalized = {
            k: self._coerce(v, allowed[k]["type"], json_document=meta.storage_kind == StorageKind.JSON_DOCUMENT)
            for k, v in values.items()
        }

        if meta.storage_kind == StorageKind.JSON_DOCUMENT:
            return await self._upsert_json_row(inst.schema_name, meta, normalized, row_id, table_slug)

        data_table = data_table_slug(meta.slug)
        fq = qualified(inst.schema_name, data_table)

        if row_id:
            exists = await self._session.execute(
                text(f"SELECT 1 FROM {fq} WHERE id = :id"),
                {"id": row_id},
            )
            if exists.scalar_one_or_none() is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Row not found")
            if normalized:
                sets = ", ".join(f"{qident(k)} = :v_{k}" for k in normalized)
                params = {f"v_{k}": v for k, v in normalized.items()}
                params["id"] = row_id
                await self._session.execute(
                    text(f"UPDATE {fq} SET {sets} WHERE id = :id"),
                    params,
                )
            await self._session.commit()
            return {"id": row_id, "table_slug": table_slug, "values": normalized}

        for col in meta.columns:
            if col["required"] and col["name"] not in normalized:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"required field missing: {col['name']}",
                )

        new_id = f"row_{uuid.uuid4().hex[:12]}"
        insert_cols = ["id"] + list(normalized.keys())
        placeholders = ", ".join(f":{c}" for c in insert_cols)
        params = {"id": new_id, **normalized}
        col_sql = ", ".join(qident(c) for c in insert_cols)
        await self._session.execute(
            text(f"INSERT INTO {fq} ({col_sql}) VALUES ({placeholders})"),
            params,
        )
        await self._session.commit()
        return {"id": new_id, "table_slug": table_slug, "values": normalized}

    async def delete_row(
        self,
        *,
        cabinet_id: str,
        table_slug: str,
        row_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> None:
        inst, meta = await self._load_table(cabinet_id, table_slug, principal, employee, write=True)
        fq = qualified(inst.schema_name, data_table_slug(meta.slug))
        res = await self._session.execute(
            text(f"DELETE FROM {fq} WHERE id = :id RETURNING id"),
            {"id": row_id},
        )
        if res.scalar_one_or_none() is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Row not found")
        await self._session.commit()

    async def _query_physical_rows(
        self,
        schema_name: str,
        meta: _TableMeta,
        limit: int,
        offset: int,
    ) -> list[dict]:
        data_table = data_table_slug(meta.slug)
        cols = ["id", "created_at"] + [c["name"] for c in meta.columns]
        select_list = ", ".join(qident(c) for c in cols)
        fq = qualified(schema_name, data_table)
        q = await self._session.execute(
            text(
                f"""
                SELECT {select_list}
                FROM {fq}
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {"lim": limit, "off": offset},
        )
        return [self._row_to_dict(r, cols) for r in q.fetchall()]

    async def _query_json_rows(
        self,
        schema_name: str,
        meta: _TableMeta,
        limit: int,
        offset: int,
    ) -> list[dict]:
        fq = qualified(schema_name, data_table_slug(meta.slug))
        q = await self._session.execute(
            text(
                f"""
                SELECT id, created_at, document
                FROM {fq}
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {"lim": limit, "off": offset},
        )
        rows: list[dict] = []
        for r in q.fetchall():
            doc = self._parse_document(r.document)
            item: dict[str, Any] = {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            item.update(doc)
            rows.append(item)
        return rows

    async def _upsert_json_row(
        self,
        schema_name: str,
        meta: _TableMeta,
        normalized: dict[str, Any],
        row_id: str | None,
        table_slug: str,
    ) -> dict:
        fq = qualified(schema_name, data_table_slug(meta.slug))

        if row_id:
            rq = await self._session.execute(
                text(f"SELECT document FROM {fq} WHERE id = :id"),
                {"id": row_id},
            )
            existing = rq.scalar_one_or_none()
            if existing is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Row not found")
            doc = self._parse_document(existing)
            doc.update(normalized)
            await self._session.execute(
                text(f"UPDATE {fq} SET document = CAST(:doc AS jsonb) WHERE id = :id"),
                {"id": row_id, "doc": json.dumps(doc, ensure_ascii=False)},
            )
            await self._session.commit()
            return {"id": row_id, "table_slug": table_slug, "values": normalized}

        for col in meta.columns:
            if col["required"] and col["name"] not in normalized:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"required field missing: {col['name']}",
                )

        new_id = f"row_{uuid.uuid4().hex[:12]}"
        await self._session.execute(
            text(
                f"""
                INSERT INTO {fq} (id, document)
                VALUES (:id, CAST(:doc AS jsonb))
                """
            ),
            {"id": new_id, "doc": json.dumps(normalized, ensure_ascii=False)},
        )
        await self._session.commit()
        return {"id": new_id, "table_slug": table_slug, "values": normalized}

    async def _load_table(
        self,
        cabinet_id: str,
        table_slug: str,
        principal: Principal,
        employee: EmployeeRow | None,
        *,
        write: bool,
    ) -> tuple[Any, _TableMeta]:
        inst = await self._access.require_access(
            cabinet_id=cabinet_id, principal=principal, employee=employee, write=write
        )
        qschema = qident(inst.schema_name)
        tq = await self._session.execute(
            text(
                f"""
                SELECT id, slug, storage_kind, status
                FROM {qschema}.meta_tables
                WHERE slug = :slug
                """
            ),
            {"slug": table_slug},
        )
        table = tq.fetchone()
        if table is None or table.status != "active":
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Table not found")
        if table.storage_kind not in {StorageKind.PHYSICAL, StorageKind.JSON_DOCUMENT}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="unsupported storage_kind",
            )

        cq = await self._session.execute(
            text(
                f"""
                SELECT name, col_type, required, unique_col
                FROM {qschema}.meta_columns
                WHERE table_id = :tid
                ORDER BY name
                """
            ),
            {"tid": table.id},
        )
        columns = [
            {
                "name": r.name,
                "type": r.col_type,
                "required": r.required,
                "unique": r.unique_col,
            }
            for r in cq.fetchall()
        ]
        return inst, _TableMeta(slug=table.slug, storage_kind=table.storage_kind, columns=columns)

    @staticmethod
    def _parse_document(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _row_to_dict(row: Any, cols: list[str]) -> dict:
        out: dict[str, Any] = {}
        for i, name in enumerate(cols):
            val = row[i]
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, Decimal):
                val = str(val)
            out[name] = val
        return out

    @staticmethod
    def _coerce(value: Any, col_type: str, *, json_document: bool = False) -> Any:
        if value is None:
            return None
        if col_type == ColumnType.BOOL:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if col_type == ColumnType.NUMBER:
            if json_document:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return Decimal(str(value))
            return Decimal(str(value))
        if col_type == ColumnType.JSON:
            if isinstance(value, (dict, list)):
                return value if json_document else json.dumps(value)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if json_document else value
                except json.JSONDecodeError:
                    return value
            return value
        return str(value)
