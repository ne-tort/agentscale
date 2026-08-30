"""Metrics BC read path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.aggregator import (
    CabinetMetricsAggregator,
    CompanyMetricsAggregator,
    ProjectMetricsAggregator,
)
from prodavan.application.metrics.read_service import MetricsReadService
from prodavan.config.settings import settings


class MetricsQuery:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = build_metrics_store()
        self._aggregator = CompanyMetricsAggregator(session)
        self._read = MetricsReadService()

    async def company_metrics(self, company_id: str) -> dict[str, Any]:
        metrics = await self._aggregator.aggregate(company_id)
        emp_ids = await self._aggregator.membership_employee_ids(company_id)
        metrics["employees_online"] = await self._read.employees_online(emp_ids)
        return metrics

    async def cabinet_metrics(self, cabinet_id: str) -> dict[str, Any]:
        return await CabinetMetricsAggregator(self._session).aggregate(cabinet_id)

    async def project_metrics(self, project_id: str) -> dict[str, Any]:
        return await ProjectMetricsAggregator(self._session).aggregate(project_id)

    async def get_project_runtime_metrics(
        self,
        project_id: str,
        *,
        allow_stale: bool = False,
    ) -> dict[str, Any] | None:
        latest = await self._store.get_project_latest(project_id)
        if latest is None:
            return None
        if not allow_stale and self._is_stale(latest):
            return None
        return latest

    async def get_project_metrics(
        self,
        project_id: str,
        *,
        window: str = "1h",
    ) -> dict[str, Any]:
        latest = await self._store.get_project_latest(project_id)
        series = await self._store.get_project_series(project_id, window)
        return {
            "project_id": project_id,
            "latest": latest,
            "series": series,
            "window": window,
            "stale": self._is_stale(latest) if latest else True,
        }

    def _is_stale(self, sample: dict[str, Any]) -> bool:
        ts = sample.get("timestamp")
        if not ts:
            return True
        try:
            text = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - dt).total_seconds()
            return age > int(settings.metrics_sample_ttl_sec)
        except (TypeError, ValueError):
            return True
