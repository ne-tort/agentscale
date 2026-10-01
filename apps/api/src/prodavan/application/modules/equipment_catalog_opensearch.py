"""Equipment catalog → OpenSearch indexing (Celery worker + enqueue).

One physical index per catalog row: namespace ``equipment``, index ``c_{row_id}``.
Reindex pipeline (v2 — reliable):

1. stamp ``status=indexing`` + progress fields (``indexed_count`` = 0,
   ``total_rows``, ``indexing_started_at`` heartbeat);
2. **open the source first** (local: download + parse tabular; remote: connect
   + COUNT(*)) — a failing source leaves the old index intact;
3. only then ``delete_index`` + ``ensure_index`` + chunked ``bulk_index``;
4. persist progress (``indexed_count``) after every bulk chunk so the UI can
   render «В процессе (x из y)» and the beat sweep can re-enqueue stale rows;
5. final ``status=ready|error`` + Kafka lifecycle events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.content.tabular_index import index_tabular_bytes
from prodavan.application.modules.equipment_catalog_search import (
    apply_column_map,
    detect_currency_value,
    is_in_stock,
    parse_price,
)
from prodavan.application.modules.module_instance_service import ModuleInstanceService
from prodavan.core.infra.opensearch_manager import get_search_index_service
from prodavan.domain.search_index.types import MAX_BULK_BATCH
from prodavan.infrastructure.files.manager import ensure_file_store

logger = logging.getLogger(__name__)

OS_NAMESPACE = "equipment"
# OpenSearch tenancy key for platform SoT catalogs (shared across companies).
PLATFORM_OS_COMPANY_ID = "platform"
REQUIRED_MAP_KEYS = ("title", "price")
DEFAULT_REINDEX_INTERVAL_HOURS = 24

_INDEX_SAFE = re.compile(r"[^a-z0-9_]+")


async def resolve_equipment_catalog_tenancy(
    session: AsyncSession,
    *,
    instance_id: str | None = None,
    inst: Any | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(company_id, cabinet_id, project_id)`` for catalog OpenSearch ops.

    Platform SoT uses ``PLATFORM_OS_COMPANY_ID`` so indexes are searchable from every
    company that resolves to the shared platform instance.
    """
    from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
    from prodavan.infrastructure.persistence.models.modules import ModuleInstanceRow
    from prodavan.infrastructure.persistence.models.projects import ProjectRow

    row = inst
    if row is None:
        if not instance_id:
            return None, None, None
        row = await session.get(ModuleInstanceRow, str(instance_id))
    if row is None:
        return None, None, None
    kind = str(row.owner_kind or "")
    oid = str(row.owner_id or "")
    if kind == "project":
        project = await session.get(ProjectRow, oid)
        if project is None:
            return None, None, None
        return str(project.company_id), str(project.cabinet_id), str(project.id)
    if kind == "cabinet":
        cab = await session.get(CabinetInstanceRow, oid)
        if cab is None or not cab.company_id:
            return None, oid or None, None
        return str(cab.company_id), str(cab.id), None
    if kind == "company":
        return oid or None, None, None
    if kind == "platform":
        return PLATFORM_OS_COMPANY_ID, None, None
    return None, None, None


def catalog_os_index_name(row_id: str) -> str:
    raw = (row_id or "").strip().lower()
    safe = _INDEX_SAFE.sub("_", raw).strip("_")
    if not safe:
        safe = "unknown"
    if not safe[0].isalpha():
        safe = f"r_{safe}"
    return f"c_{safe}"[:64]


def catalog_doc_id(catalog_id: str, source_row_key: str) -> str:
    digest = hashlib.sha1(f"{catalog_id}|{source_row_key}".encode()).hexdigest()
    return digest


def _column_map_ready(column_map: dict[str, Any] | None) -> bool:
    if not isinstance(column_map, dict):
        return False
    for key in REQUIRED_MAP_KEYS:
        val = column_map.get(key)
        if not isinstance(val, str) or not val.strip():
            return False
    return True


def _normalize_column_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _canonical_mappings() -> dict[str, Any]:
    props: dict[str, Any] = {
        "catalog_id": {"type": "keyword"},
        "source_catalog": {"type": "keyword"},
        "company_id": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "cabinet_id": {"type": "keyword"},
        "in_stock": {"type": "boolean"},
        "price_num": {"type": "double"},
        "title": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "part_number": {"type": "keyword", "fields": {"text": {"type": "text"}}},
        "brand": {"type": "keyword"},
        "price": {"type": "keyword"},
        "supplier": {"type": "keyword"},
        "lead_time": {"type": "keyword"},
        "currency": {"type": "keyword"},
        "src_hash": {"type": "keyword"},
    }
    return {"properties": props}


def _iter_sqlite_source_rows(sqlite_bytes: bytes) -> Iterator[dict[str, Any]]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(sqlite_bytes)
        cur = conn.execute("SELECT * FROM rows")
        cols = [d[0] for d in cur.description or []]
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            for row in batch:
                yield {cols[i]: row[i] for i in range(len(cols))}
    finally:
        conn.close()


def source_hash(supplier: str, title: str) -> str:
    """Stable position identity: sha1(casefold supplier|title)[:16].

    Survives 24h wipe+reindex (docs get new _ids but the same src_hash), so
    found_offers rows can be matched against the live catalog later.
    """
    key = f"{supplier.strip().casefold()}|{title.strip().casefold()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _source_row_key(mapped: dict[str, str], raw: dict[str, Any], seq: int) -> str:
    pn = (mapped.get("part_number") or "").strip()
    if pn:
        return f"pn:{pn}"
    title = (mapped.get("title") or "").strip()
    if title:
        return f"t:{hashlib.sha1(title.encode('utf-8')).hexdigest()[:16]}:{seq}"
    blob = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return f"h:{hashlib.sha1(blob.encode('utf-8')).hexdigest()}:{seq}"


def _doc_from_mapped(
    *,
    mapped: dict[str, str],
    catalog_id: str,
    catalog_name: str,
    company_id: str,
    project_id: str | None,
    cabinet_id: str | None,
) -> dict[str, Any]:
    price_num = parse_price(mapped.get("price"))
    lead = mapped.get("lead_time") or ""
    return {
        "catalog_id": catalog_id,
        "source_catalog": catalog_name,
        "company_id": company_id,
        "project_id": project_id,
        "cabinet_id": cabinet_id,
        "part_number": mapped.get("part_number") or "",
        "title": mapped.get("title") or "",
        "brand": mapped.get("brand") or "",
        "price": mapped.get("price") or "",
        "supplier": mapped.get("supplier") or "",
        "lead_time": lead,
        "currency": detect_currency_value(mapped.get("currency")) or "RUB",
        "price_num": price_num,
        "in_stock": is_in_stock(lead),
        "src_hash": source_hash(mapped.get("supplier") or "", mapped.get("title") or ""),
    }


async def run_index_equipment_catalog(
    session: AsyncSession,
    *,
    instance_id: str,
    row_id: str,
    company_id: str,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Full wipe+reindex one catalogs row into OpenSearch (source-first)."""
    inst_svc = ModuleInstanceService(session)
    row = await inst_svc.get_data_row(
        instance_id=instance_id, table_slug="catalogs", row_id=row_id
    )
    if row is None:
        return {"ok": False, "error": "row_not_found", "row_id": row_id}
    body = dict(row.get("body") or {})
    column_map = _normalize_column_map(body.get("column_map"))
    if not _column_map_ready(column_map):
        body["status"] = "draft"
        body["error"] = "column_map incomplete (title, price required)"
        await inst_svc.upsert_data_row(
            instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
        )
        await session.commit()
        return {"ok": False, "error": "column_map_incomplete", "row_id": row_id}

    index_name = catalog_os_index_name(row_id)
    catalog_name = str(body.get("name") or row_id)
    source_kind = str(body.get("source_kind") or "local").strip().lower()
    cid = (company_id or "").strip()
    if not cid:
        return {"ok": False, "error": "company_id_required", "row_id": row_id}

    from prodavan.application.search_index.publish import (
        emit_equipment_catalog_index_accepted,
        emit_equipment_catalog_index_completed,
    )

    body["status"] = "indexing"
    body["error"] = None
    body["index_name"] = f"{OS_NAMESPACE}__{index_name}"
    body["indexed_count"] = 0
    body["total_rows"] = None
    body["indexing_started_at"] = datetime.now(UTC).isoformat()
    await inst_svc.upsert_data_row(
        instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
    )
    await session.commit()
    await emit_equipment_catalog_index_accepted(
        session=None,
        company_id=cid,
        cabinet_id=cabinet_id,
        project_id=project_id,
        catalog_row_id=row_id,
        instance_id=instance_id,
        index=index_name,
    )

    svc = get_search_index_service()

    # --- 1) open the source BEFORE touching the old index ------------------
    # An unreachable remote DSN / missing file must NOT leave the catalog with
    # a deleted (empty) index: fail early, keep previous documents intact.
    try:
        if source_kind in ("remote", "remote_sql"):
            source = await _open_remote_source(body, cabinet_id=cabinet_id or "")
        else:
            source = await _open_local_source(body)
    except Exception as exc:
        logger.exception("equipment os source open failed row=%s", row_id)
        body["status"] = "error"
        body["error"] = str(exc)[:500]
        body["total_rows"] = None
        body["indexed_count"] = 0
        await inst_svc.upsert_data_row(
            instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
        )
        await session.commit()
        await emit_equipment_catalog_index_completed(
            session=None,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            catalog_row_id=row_id,
            instance_id=instance_id,
            index=index_name,
            ok=False,
            error=str(exc)[:300],
        )
        return {"ok": False, "error": str(exc)[:300], "row_id": row_id}

    # Expose the discovered row count early so UI shows «0 из y».
    if source.total_rows is not None:
        body["total_rows"] = source.total_rows
        await inst_svc.upsert_data_row(
            instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
        )
        await session.commit()

    async def _persist_progress(indexed: int, total: int | None) -> None:
        """Heartbeat: row body update after each bulk chunk (x из y)."""
        body["indexed_count"] = indexed
        if total is not None:
            body["total_rows"] = total
        await inst_svc.upsert_data_row(
            instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
        )
        await session.commit()

    try:
        # --- 2) wipe + recreate (source already validated) -----------------
        try:
            await svc.delete_index(
                namespace=OS_NAMESPACE,
                index=index_name,
                company_id=cid,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )
        except Exception:
            logger.exception("equipment os delete_index ignored row=%s", row_id)

        await svc.ensure_index(
            namespace=OS_NAMESPACE,
            index=index_name,
            mappings=_canonical_mappings(),
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )

        # --- 3) chunked bulk with persisted progress -----------------------
        indexed, indexed_suppliers = await _index_opened_source(
            svc=svc,
            source=source,
            column_map=column_map,
            catalog_id=row_id,
            catalog_name=catalog_name,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            index_name=index_name,
            on_progress=_persist_progress,
        )
    except Exception as exc:
        logger.exception("equipment os index failed row=%s", row_id)
        body["status"] = "error"
        body["error"] = str(exc)[:500]
        await inst_svc.upsert_data_row(
            instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
        )
        await session.commit()
        await emit_equipment_catalog_index_completed(
            session=None,
            company_id=cid,
            cabinet_id=cabinet_id,
            project_id=project_id,
            catalog_row_id=row_id,
            instance_id=instance_id,
            index=index_name,
            ok=False,
            error=str(exc)[:300],
        )
        return {"ok": False, "error": str(exc)[:300], "row_id": row_id}
    finally:
        await source.aclose()

    body["status"] = "ready"
    body["error"] = None
    body["row_count"] = indexed
    body["indexed_count"] = indexed
    if source.total_rows is not None:
        body["total_rows"] = source.total_rows
    body["last_indexed_at"] = datetime.now(UTC).isoformat()
    body["index_name"] = f"{OS_NAMESPACE}__{index_name}"
    await inst_svc.upsert_data_row(
        instance_id=instance_id, table_slug="catalogs", row_id=row_id, body=body
    )
    await session.commit()
    await emit_equipment_catalog_index_completed(
        session=None,
        company_id=cid,
        cabinet_id=cabinet_id,
        project_id=project_id,
        catalog_row_id=row_id,
        instance_id=instance_id,
        index=index_name,
        ok=True,
        indexed=indexed,
    )
    sellers_mapped = 0
    try:
        sellers_mapped = await _sync_sellers_from_catalog(
            session,
            instance_id=instance_id,
            suppliers=indexed_suppliers,
        )
    except Exception:
        logger.exception("equipment catalog index: sellers auto-fill failed")
    return {
        "ok": True,
        "row_id": row_id,
        "indexed": indexed,
        "index": f"{OS_NAMESPACE}__{index_name}",
        "sellers_mapped": sellers_mapped,
    }


# --- sources (opened before the index is touched) --------------------------


class _LocalTabularSource:
    """Parsed local CSV/XLSX source: in-memory sqlite + row count."""

    def __init__(self, tabular: Any, total_rows: int) -> None:
        self._tabular = tabular
        self.total_rows = total_rows

    async def aiter_rows(self) -> AsyncIterator[dict[str, Any]]:
        for row in _iter_sqlite_source_rows(self._tabular.sqlite_bytes):
            yield row

    async def aclose(self) -> None:
        return None


class _RemoteSqlSource:
    """Opened remote PostgreSQL source: connection + optional COUNT(*)."""

    def __init__(self, conn: Any, from_sql: str, total_rows: int | None) -> None:
        self._conn = conn
        self._from_sql = from_sql
        self.total_rows = total_rows

    async def aiter_rows(self) -> AsyncIterator[dict[str, Any]]:
        stmt = await self._conn.prepare(f"SELECT * FROM {self._from_sql}")  # noqa: S608
        async with self._conn.transaction():
            async for record in stmt.cursor(prefetch=500):
                yield dict(record)

    async def aclose(self) -> None:
        try:
            await self._conn.close()
        except Exception:
            logger.exception("remote source close failed")


def _sqlite_row_count(sqlite_bytes: bytes) -> int:
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(sqlite_bytes)
        row = conn.execute("SELECT COUNT(*) FROM rows").fetchone()
        return int((row or (0,))[0] or 0)
    finally:
        conn.close()


async def _open_local_source(body: dict[str, Any]) -> _LocalTabularSource:
    """Download + parse the local tabular file; writes header columns to body."""
    file_ref = body.get("source_file")
    if not isinstance(file_ref, dict):
        raise ValueError("source_file missing")
    storage_key = str(file_ref.get("storage_key") or "")
    if not storage_key:
        raise ValueError("source_file.storage_key missing")
    filename = str(file_ref.get("filename") or "data.csv")
    raw = ensure_file_store().get_bytes_sync(storage_key)
    tabular = index_tabular_bytes(raw, filename=filename)
    body["columns_json"] = json.dumps(tabular.columns, ensure_ascii=False)
    body["indexed_source_key"] = storage_key
    return _LocalTabularSource(tabular, _sqlite_row_count(tabular.sqlite_bytes))


async def _open_remote_source(
    body: dict[str, Any], *, cabinet_id: str
) -> _RemoteSqlSource:
    """Resolve secrets, connect to the remote SQL and COUNT(*) rows."""
    import asyncpg

    from prodavan.application.content.remote_sql_probe import (
        quote_ident,
        resolve_remote_catalog_target,
    )
    from prodavan.application.pod_service.container_env_resolver import field_value_as_secret_ref
    from prodavan.infrastructure.secrets.cabinet_secret_store import assert_cabinet_secret_scope
    from prodavan.infrastructure.secrets.store import get_secret_store

    secret_ref = field_value_as_secret_ref(body.get("remote_dsn"))
    if not secret_ref:
        raise ValueError("remote_dsn missing")
    if secret_ref.startswith(("file://cabinet_secrets/", "vault://cabinet_secrets/")):
        if not cabinet_id:
            raise ValueError("cabinet_id required for cabinet_secrets DSN")
        assert_cabinet_secret_scope(secret_ref, cabinet_id)
    dsn = get_secret_store().get(secret_ref)
    if not dsn:
        raise ValueError("remote_dsn secret empty")

    pwd_ref = field_value_as_secret_ref(body.get("remote_password"))
    password = get_secret_store().get(pwd_ref) if pwd_ref else ""
    user = str(body.get("remote_user") or "")
    database = str(body.get("remote_database") or "")
    table = str(body.get("remote_table") or "")
    connect_dsn, schema, tbl = resolve_remote_catalog_target(
        dsn=dsn,
        remote_table_field=table,
        remote_database_field=database,
        remote_user_field=user,
        remote_password_field=password or "",
    )
    if schema:
        from_sql = f"{quote_ident(schema)}.{quote_ident(tbl)}"
    else:
        from_sql = quote_ident(tbl)

    conn = await asyncpg.connect(connect_dsn)
    total: int | None
    try:
        row = await conn.fetchrow(f"SELECT COUNT(*) FROM {from_sql}")  # noqa: S608
        total = int((row or (0,))[0] or 0)
    except Exception:
        logger.exception("remote COUNT(*) failed — progress will be indeterminate")
        total = None
    return _RemoteSqlSource(conn, from_sql, total)


async def _index_opened_source(
    *,
    svc: Any,
    source: _LocalTabularSource | _RemoteSqlSource,
    column_map: dict[str, str],
    catalog_id: str,
    catalog_name: str,
    company_id: str,
    cabinet_id: str | None,
    project_id: str | None,
    index_name: str,
    on_progress: Callable[[int, int | None], Awaitable[None]] | None = None,
) -> tuple[int, set[str]]:
    """Chunked bulk_index over an already-opened source, with progress heartbeats.

    Returns (indexed, distinct supplier names seen in the source).
    """
    batch: list[dict[str, Any]] = []
    total = 0
    seq = 0
    suppliers: set[str] = set()
    async for raw_row in source.aiter_rows():
        seq += 1
        mapped = apply_column_map(raw_row, column_map)
        if not (mapped.get("title") or "").strip():
            continue
        supplier_name = (mapped.get("supplier") or "").strip()
        if supplier_name:
            suppliers.add(supplier_name)
        doc = _doc_from_mapped(
            mapped=mapped,
            catalog_id=catalog_id,
            catalog_name=catalog_name,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
        )
        doc_id = catalog_doc_id(catalog_id, _source_row_key(mapped, raw_row, seq))
        batch.append({"doc_id": doc_id, "document": doc})
        if len(batch) >= MAX_BULK_BATCH:
            result = await svc.bulk_index(
                namespace=OS_NAMESPACE,
                index=index_name,
                documents=batch,
                company_id=company_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
                refresh=False,
            )
            total += result.indexed
            batch = []
            if on_progress is not None:
                await on_progress(total, source.total_rows)
    if batch:
        result = await svc.bulk_index(
            namespace=OS_NAMESPACE,
            index=index_name,
            documents=batch,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            refresh=True,
        )
        total += result.indexed
        if on_progress is not None:
            await on_progress(total, source.total_rows)
    elif total:
        # force refresh after last chunk
        await svc.search(
            namespace=OS_NAMESPACE,
            index=index_name,
            query={"match_all": {}},
            size=1,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
        )
    return total, suppliers


async def _sync_sellers_from_catalog(
    session: AsyncSession,
    *,
    instance_id: str,
    suppliers: set[str],
) -> int:
    """Auto-fill «Поставщики» (trusted_sellers) from a freshly indexed catalog.

    Dedup by name/aliases (casefold); existing rows are never touched.
    Deterministic row ids make the sync idempotent across re-indexes.
    """
    if not suppliers:
        return 0
    inst_svc = ModuleInstanceService(session)

    def _alias_tokens(raw: Any) -> set[str]:
        if isinstance(raw, list):
            toks = [str(x).strip() for x in raw]
        elif isinstance(raw, str):
            toks = raw.replace(";", ",").split(",")
        else:
            toks = []
        return {t.casefold() for t in toks if t}

    known: set[str] = set()
    try:
        rows = await inst_svc.list_data_rows(
            instance_id=instance_id, table_slug="trusted_sellers"
        )
    except Exception:
        rows = []
    for r in rows:
        body = r.get("body") if isinstance(r, dict) else {}
        if not isinstance(body, dict):
            continue
        name = str(body.get("name") or "").strip()
        if not name:
            continue
        known |= {name.casefold()} | _alias_tokens(body.get("aliases"))

    created = 0
    for name in sorted(suppliers):
        if not name.strip() or name.casefold() in known:
            continue
        row_id = f"sell_{hashlib.sha1(name.strip().casefold().encode('utf-8')).hexdigest()[:12]}"
        await inst_svc.upsert_data_row(
            instance_id=instance_id,
            table_slug="trusted_sellers",
            row_id=row_id,
            body={"name": name.strip(), "is_enabled": True},
            created_by="module_seed",
        )
        known.add(name.casefold())
        created += 1
    if created:
        await session.commit()
        logger.info("equipment catalog index: mapped %d new suppliers", created)
    return created


def enqueue_or_run_index_equipment_catalog(
    *,
    instance_id: str,
    row_id: str,
    company_id: str,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue Celery job or run inline when Celery is disabled."""
    from prodavan.core.infra.worker_manager import get_worker_manager
    from prodavan.core.jobs import names as job_names
    from prodavan.core.jobs.idempotency import index_equipment_catalog_task_id

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {
            "enqueued": False,
            "reason": "celery_disabled",
            "inline": True,
            "instance_id": instance_id,
            "row_id": row_id,
        }
    task_id = index_equipment_catalog_task_id(row_id)
    mgr.send_task(
        job_names.INDEX_EQUIPMENT_CATALOG,
        kwargs={
            "instance_id": instance_id,
            "row_id": row_id,
            "company_id": company_id,
            "cabinet_id": cabinet_id,
            "project_id": project_id,
        },
        task_id=task_id,
    )
    return {
        "enqueued": True,
        "task": job_names.INDEX_EQUIPMENT_CATALOG,
        "task_id": task_id,
        "row_id": row_id,
    }


async def delete_equipment_catalog_index(
    *,
    row_id: str,
    company_id: str,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> bool:
    svc = get_search_index_service()
    try:
        return await svc.delete_index(
            namespace=OS_NAMESPACE,
            index=catalog_os_index_name(row_id),
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
    except Exception:
        logger.exception(
            "delete_equipment_catalog_index failed row_id=%s company=%s",
            row_id,
            company_id,
        )
        return False


async def cleanup_equipment_indexes_for_instance(
    *,
    session: Any,
    instance_id: str,
    company_id: str,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> list[str]:
    """Delete OpenSearch indexes for all catalogs rows on a module instance."""
    from sqlalchemy import select

    from prodavan.infrastructure.persistence.models.modules import ModuleInstanceDataRow

    q = await session.execute(
        select(ModuleInstanceDataRow).where(
            ModuleInstanceDataRow.instance_id == instance_id,
            ModuleInstanceDataRow.table_slug == "catalogs",
        )
    )
    deleted: list[str] = []
    for row in q.scalars().all():
        rid = str(row.row_id or "").strip()
        if not rid:
            continue
        ok = await delete_equipment_catalog_index(
            row_id=rid,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        if ok:
            deleted.append(rid)
    return deleted


async def reconcile_orphan_equipment_indexes(
    *,
    session: Any,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Delete physical equipment indexes with no matching catalogs row in DB."""
    from sqlalchemy import select

    from prodavan.domain.search_index.types import physical_index
    from prodavan.infrastructure.persistence.models.modules import ModuleInstanceDataRow

    svc = get_search_index_service()
    physical_names = await svc.list_indexes(
        namespace=OS_NAMESPACE, company_id=company_id
    )
    live_physical: set[str] = set()
    q = await session.execute(
        select(ModuleInstanceDataRow).where(ModuleInstanceDataRow.table_slug == "catalogs")
    )
    for row in q.scalars().all():
        body = row.body if isinstance(row.body, dict) else {}
        stored = str(body.get("index_name") or "").strip()
        rid = str(row.row_id or "").strip()
        if stored:
            live_physical.add(stored)
        if rid:
            live_physical.add(physical_index(OS_NAMESPACE, catalog_os_index_name(rid)))

    prefix = f"{OS_NAMESPACE}__"
    deleted: list[str] = []
    kept = 0
    for name in physical_names:
        if not name.startswith(prefix):
            continue
        if name in live_physical:
            kept += 1
            continue
        logical = name[len(prefix) :]
        try:
            if company_id:
                ok = await svc.delete_index(
                    namespace=OS_NAMESPACE,
                    index=logical,
                    company_id=company_id,
                )
            else:
                # Admin reconcile across tenants — drop orphan physical index directly.
                ok = await svc._store.delete_index(namespace=OS_NAMESPACE, index=logical)
        except Exception:
            logger.exception("reconcile delete failed index=%s", name)
            continue
        if ok:
            deleted.append(name)
    return {"deleted": deleted, "kept": kept, "scanned": len(physical_names)}


async def extract_local_columns(body: dict[str, Any]) -> list[str]:
    """Sync header probe for column_map UI (does not write OpenSearch)."""
    file_ref = body.get("source_file")
    if not isinstance(file_ref, dict):
        return []
    storage_key = str(file_ref.get("storage_key") or "")
    if not storage_key:
        return []
    filename = str(file_ref.get("filename") or "data.csv")
    raw = ensure_file_store().get_bytes_sync(storage_key)
    return list(index_tabular_bytes(raw, filename=filename).columns)


def column_map_ready(body: dict[str, Any]) -> bool:
    return _column_map_ready(_normalize_column_map(body.get("column_map")))
