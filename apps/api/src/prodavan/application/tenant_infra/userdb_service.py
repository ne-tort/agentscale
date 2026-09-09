"""Tenant User DB — separate Postgres database, schema per project, no raw SQL."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from prodavan.application.pod_identity.bridge import SCOPE_INFRA_USERDB, PodBridgeClaims
from prodavan.application.tenant_infra.publish import emit_tenant_infra_event
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService, enforce_ops_rate, quota_exceeded
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ALLOWED_TYPES = {
    "text": "TEXT",
    "int": "BIGINT",
    "float": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "timestamptz": "TIMESTAMPTZ",
    "jsonb": "JSONB",
}

_engine: AsyncEngine | None = None
_memory_tables: dict[str, dict[str, list[dict[str, Any]]]] = {}


def _schema_name(project_id: str) -> str:
    raw = re.sub(r"[^a-z0-9_]", "_", (project_id or "").lower())
    raw = raw.strip("_") or "x"
    name = f"p_{raw}"
    return name[:63]


def _ident(name: str, *, kind: str = "identifier") -> str:
    n = (name or "").strip().lower()
    if not _IDENT_RE.match(n):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"invalid {kind}: {name!r}",
        )
    return n


def _userdb_url() -> str | None:
    explicit = (settings.userdb_url or "").strip()
    if explicit:
        return explicit
    base = (settings.database_url or "").strip()
    if not base:
        return None
    # Derive sibling DB name prodavan_userdb from app DSN.
    if "/" not in base.rsplit("@", 1)[-1]:
        return None
    prefix, _db = base.rsplit("/", 1)
    return f"{prefix}/prodavan_userdb"


def get_userdb_engine() -> AsyncEngine | None:
    global _engine
    if _engine is not None:
        return _engine
    url = _userdb_url()
    if not url:
        return None
    try:
        _engine = create_async_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
        return _engine
    except Exception:
        logger.exception("userdb engine init failed")
        return None


class TenantUserDbService:
    def _require(self, bridge: PodBridgeClaims, project_id: str) -> None:
        bridge.require_project(project_id)
        bridge.require_scope(SCOPE_INFRA_USERDB)

    async def _quota(self, bridge: PodBridgeClaims, session: AsyncSession | None):
        return await TenantInfraQuotaService(session).get_quota(bridge.company_id)

    async def _ensure_schema(self, schema: str) -> AsyncEngine:
        engine = get_userdb_engine()
        if engine is None:
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                title="Service Unavailable",
                status=503,
                detail="userdb unavailable",
            )
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        return engine

    async def create_table(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        table: str,
        columns: list[dict[str, str]],
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="userdb",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.userdb_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        schema = _schema_name(bridge.project_id)
        table_name = _ident(table, kind="table")
        if not columns:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="columns required")
        col_defs: list[str] = ['"id" BIGSERIAL PRIMARY KEY']
        for col in columns:
            cname = _ident(str(col.get("name") or ""), kind="column")
            if cname == "id":
                continue
            ctype = _ALLOWED_TYPES.get(str(col.get("type") or "").lower())
            if not ctype:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unsupported column type: {col.get('type')!r}",
                )
            col_defs.append(f'"{cname}" {ctype}')
        engine = get_userdb_engine()
        if engine is None:
            bucket = _memory_tables.setdefault(schema, {})
            if len(bucket) >= quota.userdb_max_tables:
                raise quota_exceeded(f"userdb max tables {quota.userdb_max_tables} exceeded")
            if table_name in bucket:
                raise AppError(code="CONFLICT", title="Conflict", status=409, detail="table exists")
            bucket[table_name] = []
            return {"schema": schema, "table": table_name, "ok": True, "mode": "memory"}
        engine = await self._ensure_schema(schema)
        async with engine.connect() as conn:
            existing = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": schema},
            )
            count = int(existing.scalar_one())
            if count >= quota.userdb_max_tables:
                raise quota_exceeded(f"userdb max tables {quota.userdb_max_tables} exceeded")
        ddl = f'CREATE TABLE "{schema}"."{table_name}" ({", ".join(col_defs)})'
        async with engine.begin() as conn:
            await conn.execute(text(ddl))
        await emit_tenant_infra_event(
            session=session,
            event_type="tenant_infra.op",
            company_id=bridge.company_id,
            cabinet_id=bridge.cabinet_id,
            project_id=bridge.project_id,
            payload={"op": "userdb.create_table", "table": table_name},
        )
        return {"schema": schema, "table": table_name, "ok": True}

    async def list_tables(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="userdb",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.userdb_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        schema = _schema_name(bridge.project_id)
        engine = get_userdb_engine()
        if engine is None:
            return {"schema": schema, "tables": list((_memory_tables.get(schema) or {}).keys())}
        await self._ensure_schema(schema)
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema ORDER BY table_name"
                ),
                {"schema": schema},
            )
            tables = [str(r[0]) for r in rows.fetchall()]
        return {"schema": schema, "tables": tables}

    async def insert(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        table: str,
        row: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="userdb",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.userdb_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        raw = json.dumps(row, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > quota.userdb_max_row_bytes:
            raise quota_exceeded(f"userdb row exceeds {quota.userdb_max_row_bytes} bytes")
        schema = _schema_name(bridge.project_id)
        table_name = _ident(table, kind="table")
        engine = get_userdb_engine()
        if engine is None:
            bucket = (_memory_tables.get(schema) or {}).get(table_name)
            if bucket is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="table not found")
            if len(bucket) >= quota.userdb_max_rows_per_table:
                raise quota_exceeded(f"userdb max rows {quota.userdb_max_rows_per_table} exceeded")
            new_id = len(bucket) + 1
            item = {"id": new_id, **row}
            bucket.append(item)
            return {"table": table_name, "row": item}
        await self._ensure_schema(schema)
        cols = [_ident(k, kind="column") for k in row.keys() if k != "id"]
        if not cols:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="row empty")
        async with engine.connect() as conn:
            cnt = await conn.execute(
                text(f'SELECT COUNT(*) FROM "{schema}"."{table_name}"')
            )
            if int(cnt.scalar_one()) >= quota.userdb_max_rows_per_table:
                raise quota_exceeded(f"userdb max rows {quota.userdb_max_rows_per_table} exceeded")
        placeholders = ", ".join(f":{c}" for c in cols)
        col_sql = ", ".join(f'"{c}"' for c in cols)
        sql = (
            f'INSERT INTO "{schema}"."{table_name}" ({col_sql}) '
            f"VALUES ({placeholders}) RETURNING *"
        )
        params = {c: row[c] for c in cols}
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            inserted = dict(result.mappings().one())
        return {"table": table_name, "row": inserted}

    async def select(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        table: str,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="userdb",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.userdb_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        schema = _schema_name(bridge.project_id)
        table_name = _ident(table, kind="table")
        lim = min(max(1, int(limit)), 200)
        engine = get_userdb_engine()
        if engine is None:
            rows = list((_memory_tables.get(schema) or {}).get(table_name) or [])[:lim]
            return {"table": table_name, "items": rows, "count": len(rows)}
        await self._ensure_schema(schema)
        async with engine.connect() as conn:
            result = await conn.execute(
                text(f'SELECT * FROM "{schema}"."{table_name}" ORDER BY id LIMIT :lim'),
                {"lim": lim},
            )
            items = [dict(r) for r in result.mappings().all()]
        return {"table": table_name, "items": items, "count": len(items)}

    async def drop_table(
        self,
        *,
        bridge: PodBridgeClaims,
        project_id: str,
        table: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require(bridge, project_id)
        quota = await self._quota(bridge, session)
        await enforce_ops_rate(
            plane="userdb",
            company_id=bridge.company_id,
            project_id=bridge.project_id,
            limit=quota.userdb_ops_per_minute,
            acting_employee_id=bridge.acting_employee_id,
        )
        schema = _schema_name(bridge.project_id)
        table_name = _ident(table, kind="table")
        engine = get_userdb_engine()
        if engine is None:
            bucket = _memory_tables.get(schema) or {}
            bucket.pop(table_name, None)
            return {"table": table_name, "ok": True}
        await self._ensure_schema(schema)
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{table_name}" CASCADE'))
        return {"table": table_name, "ok": True}

    async def purge_project(self, *, company_id: str, project_id: str) -> int:
        del company_id  # schema is project-scoped
        schema = _schema_name(project_id)
        engine = get_userdb_engine()
        if engine is None:
            existed = 1 if schema in _memory_tables else 0
            _memory_tables.pop(schema, None)
            return existed
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        return 1
