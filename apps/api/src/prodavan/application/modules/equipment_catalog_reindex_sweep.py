"""Beat sweep: enqueue OpenSearch reindex for due remote equipment catalogs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.modules.equipment_catalog_opensearch import (
    enqueue_or_run_index_equipment_catalog,
)
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.modules import (
    ModuleInstanceDataRow,
    ModuleInstanceRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

logger = logging.getLogger(__name__)

_CATALOGS_SLUG = "catalogs"
_MODULE_ID = "mod_equipment"


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
        last = last.replace(tzinfo=timezone.utc)
    age_h = (now - last).total_seconds() / 3600.0
    return age_h >= _interval_hours(row)


async def _tenancy_for_instance(
    session: AsyncSession, inst: ModuleInstanceRow
) -> tuple[str | None, str | None, str | None]:
    """Return (company_id, cabinet_id, project_id)."""
    if inst.owner_kind == "project":
        project = await session.get(ProjectRow, str(inst.owner_id))
        if project is None:
            return None, None, None
        return str(project.company_id), str(project.cabinet_id), str(project.id)
    if inst.owner_kind == "cabinet":
        cab = await session.get(CabinetInstanceRow, str(inst.owner_id))
        if cab is None or not cab.company_id:
            return None, str(inst.owner_id), None
        return str(cab.company_id), str(cab.id), None
    if inst.owner_kind == "company":
        return str(inst.owner_id), None, None
    return None, None, None


async def sweep_due_equipment_catalogs(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
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
    for data_row, inst in result.all():
        body = data_row.body if isinstance(data_row.body, dict) else {}
        scanned += 1
        if not _due(body, now=now):
            continue
        company_id, cabinet_id, project_id = await _tenancy_for_instance(session, inst)
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
    logger.info("equipment_catalog_reindex_sweep scanned=%s enqueued=%s", scanned, enqueued)
    return {"ok": True, "scanned": scanned, "enqueued": enqueued}
