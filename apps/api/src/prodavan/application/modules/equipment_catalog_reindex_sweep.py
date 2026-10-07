"""Beat sweep: enqueue OpenSearch reindex for due remote equipment catalogs.

Also heals rows stuck in ``status=indexing``: when the Celery worker dies
mid-index (deploy / SIGKILL) nothing would ever write the final status — the
catalog would show «В процессе» forever. The sweep re-enqueues rows whose
``indexing_started_at`` heartbeat is older than ``STALE_INDEXING_MINUTES``
(legacy rows without the heartbeat are treated as stale once).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_opensearch import (
    enqueue_or_run_index_equipment_catalog,
    resolve_equipment_catalog_tenancy,
)
from prodavan.infrastructure.persistence.models.modules import (
    ModuleInstanceDataRow,
    ModuleInstanceRow,
)

logger = logging.getLogger(__name__)

_CATALOGS_SLUG = "catalogs"
_MODULE_ID = "mod_equipment"

# A row in ``indexing`` with no fresh heartbeat for this long is considered
# dead (worker restarted mid-index) and is re-enqueued by the sweep.
STALE_INDEXING_MINUTES = 45.0


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _interval_hours(row: dict[str, Any]) -> float:
    raw = row.get("reindex_interval_hours")
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = 24.0
    return max(hours, 1.0)


def _stale_indexing(row: dict[str, Any], *, now: datetime) -> bool:
    """True when a row is stuck in ``indexing``/``queued`` (worker died
    mid-index, or the queued task was lost with a purged broker queue)."""
    if str(row.get("status") or "").strip().lower() not in {"indexing", "queued"}:
        return False
    started = _parse_iso(row.get("indexing_started_at"))
    if started is None:
        # Legacy row created before the heartbeat feature — treat as stale so
        # it gets re-enqueued exactly once and gains the heartbeat fields.
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    age_min = (now - started).total_seconds() / 60.0
    return age_min >= STALE_INDEXING_MINUTES


def _due(row: dict[str, Any], *, now: datetime) -> bool:
    kind = str(row.get("source_kind") or "").strip().lower()
    if kind not in ("remote", "remote_sql"):
        return False
    if bool(row.get("paused")):
        return False
    if str(row.get("status") or "").strip().lower() == "indexing":
        return False
    last = _parse_iso(row.get("last_indexed_at"))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    age_h = (now - last).total_seconds() / 3600.0
    return age_h >= _interval_hours(row)


async def sweep_due_equipment_catalogs(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    enqueued = 0
    scanned = 0
    result = await session.execute(
        select(ModuleInstanceDataRow, ModuleInstanceRow)
        .join(
            ModuleInstanceRow,
            ModuleInstanceRow.id == ModuleInstanceDataRow.instance_id,
        )
        .where(
            ModuleInstanceDataRow.table_slug == _CATALOGS_SLUG,
            ModuleInstanceRow.module_id == _MODULE_ID,
        )
    )
    requeued = 0
    for data_row, inst in result.all():
        body = data_row.body if isinstance(data_row.body, dict) else {}
        scanned += 1
        if _stale_indexing(body, now=now):
            # Worker died mid-index (deploy/restart): re-enqueue and refresh
            # the heartbeat so the next sweep pass does not double-enqueue.
            company_id, cabinet_id, project_id = await resolve_equipment_catalog_tenancy(
                session, inst=inst
            )
            if not company_id:
                continue
            row_id = str(data_row.row_id or "").strip()
            if not row_id:
                continue
            if bool(body.get("paused")):
                body["status"] = "error"
                body["error"] = "indexing interrupted (worker restart)"
                body["indexed_count"] = 0
                data_row.body = dict(body)
                await session.commit()
                continue
            body["indexing_started_at"] = now.isoformat()
            data_row.body = dict(body)
            await session.commit()
            enqueue_or_run_index_equipment_catalog(
                instance_id=str(inst.id),
                row_id=row_id,
                company_id=company_id,
                cabinet_id=cabinet_id,
                project_id=project_id,
            )
            requeued += 1
            logger.info(
                "equipment_catalog_reindex_sweep stale re-enqueued row=%s", row_id
            )
            continue
        if not _due(body, now=now):
            continue
        company_id, cabinet_id, project_id = await resolve_equipment_catalog_tenancy(
            session, inst=inst
        )
        if not company_id:
            continue
        row_id = str(data_row.row_id or "").strip()
        if not row_id:
            continue
        enqueue_or_run_index_equipment_catalog(
            instance_id=str(inst.id),
            row_id=row_id,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        enqueued += 1
    logger.info(
        "equipment_catalog_reindex_sweep scanned=%s enqueued=%s requeued=%s",
        scanned,
        enqueued,
        requeued,
    )
    return {"ok": True, "scanned": scanned, "enqueued": enqueued, "requeued": requeued}
