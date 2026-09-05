"""Periodic object-store size snapshots → metrics.storage.snapshot."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.storage_metrics import company_blob_storage_bytes
from prodavan.application.metrics.accumulator import MetricsAccumulator
from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
from prodavan.application.metrics.publish import schedule_storage_snapshot
from prodavan.core.infra.object_keys import cabinet_packages_prefix
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.metrics.types import ENTITY_CABINET, ENTITY_COMPANY, ENTITY_PROJECT
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import workspace_tree_bytes

logger = logging.getLogger(__name__)


class StorageMetricsSampler:
    """Scan project/cabinet/company blob prefixes and publish absolute snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sample_all(self, *, limit: int = 500, apply_local: bool = False) -> dict[str, Any]:
        """Publish Kafka snapshots; when ``apply_local`` also write Redis immediately (backfill)."""
        projects = (
            await self._session.execute(
                select(ProjectRow)
                .where(ProjectRow.status != ProjectStatus.DELETED)
                .limit(limit)
            )
        ).scalars().all()

        cabinet_project_bytes: dict[str, int] = {}
        company_project_bytes: dict[str, int] = {}
        published = 0
        acc = MetricsAccumulator(build_counter_store()) if apply_local else None

        for row in projects:
            key = (row.workspace_key or "").strip()
            size = workspace_tree_bytes(key) if key else 0
            if row.cabinet_id:
                cabinet_project_bytes[row.cabinet_id] = cabinet_project_bytes.get(row.cabinet_id, 0) + size
            if row.company_id:
                company_project_bytes[row.company_id] = company_project_bytes.get(row.company_id, 0) + size
            await self._emit(
                acc,
                entity_type=ENTITY_PROJECT,
                entity_id=row.id,
                bytes_value=size,
                company_id=row.company_id,
                cabinet_id=row.cabinet_id,
                project_id=row.id,
            )
            published += 1

        cabinets = (
            await self._session.execute(
                select(CabinetInstanceRow)
                .where(CabinetInstanceRow.status == CabinetStatus.ACTIVE)
                .limit(limit)
            )
        ).scalars().all()

        store = ensure_file_store()
        company_pkg: dict[str, int] = {}
        for cab in cabinets:
            pkg = 0
            try:
                pkg = int(store.prefix_size_sync(cabinet_packages_prefix(cab.id)))
            except Exception:
                logger.exception("cabinet packages size failed cabinet=%s", cab.id)
            total = cabinet_project_bytes.get(cab.id, 0) + pkg
            if cab.company_id:
                company_pkg[cab.company_id] = company_pkg.get(cab.company_id, 0) + pkg
            await self._emit(
                acc,
                entity_type=ENTITY_CABINET,
                entity_id=cab.id,
                bytes_value=total,
                company_id=cab.company_id,
                cabinet_id=cab.id,
            )
            published += 1

        companies = (
            await self._session.execute(
                select(CompanyRow).where(CompanyRow.deleted_at.is_(None)).limit(limit)
            )
        ).scalars().all()

        for co in companies:
            total = company_project_bytes.get(co.id, 0) + company_pkg.get(co.id, 0)
            if total == 0 and co.id not in company_project_bytes:
                keys_q = await self._session.execute(
                    select(ProjectRow.workspace_key).where(
                        ProjectRow.company_id == co.id,
                        ProjectRow.status != ProjectStatus.DELETED,
                    )
                )
                cab_q = await self._session.execute(
                    select(CabinetInstanceRow.id).where(
                        CabinetInstanceRow.company_id == co.id,
                        CabinetInstanceRow.status == CabinetStatus.ACTIVE,
                    )
                )
                total = company_blob_storage_bytes(
                    workspace_keys=list(keys_q.scalars().all()),
                    cabinet_ids=list(cab_q.scalars().all()),
                )
            await self._emit(
                acc,
                entity_type=ENTITY_COMPANY,
                entity_id=co.id,
                bytes_value=total,
                company_id=co.id,
            )
            published += 1

        return {"published": published, "projects": len(projects), "cabinets": len(cabinets)}

    async def _emit(
        self,
        acc: MetricsAccumulator | None,
        *,
        entity_type: str,
        entity_id: str,
        bytes_value: int,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        schedule_storage_snapshot(
            self._session,
            entity_type=entity_type,
            entity_id=entity_id,
            bytes_value=bytes_value,
            company_id=company_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
        )
        if acc is not None:
            await acc.apply_storage_snapshot(
                entity_type=entity_type,
                entity_id=entity_id,
                bytes_value=bytes_value,
                company_id=company_id,
                cabinet_id=cabinet_id,
            )
