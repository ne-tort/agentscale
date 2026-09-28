"""Metrics BC read path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.aggregator import (
    CabinetMetricsAggregator,
    CompanyMetricsAggregator,
    EmployeeMetricsAggregator,
    ProjectMetricsAggregator,
    SessionMetricsAggregator,
)
from prodavan.application.metrics.read_service import MetricsReadService
from prodavan.config.settings import settings
from prodavan.domain.metrics.types import METRIC_WINDOWS


class MetricsQuery:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = build_metrics_store()
        self._counters = build_counter_store()
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

    async def session_metrics(self, session_id: str) -> dict[str, Any]:
        return await SessionMetricsAggregator(self._session).aggregate(session_id)

    async def employee_metrics(self, employee_id: str) -> dict[str, Any]:
        return await EmployeeMetricsAggregator(self._session).aggregate(employee_id)

    async def counter_series(
        self,
        *,
        metric: str,
        entity_type: str,
        entity_id: str,
        window: str = "7d",
    ) -> dict[str, Any]:
        win = window if window in METRIC_WINDOWS or window == "7d" else "7d"
        series = await self._counters.get_counter_series(
            entity_type, entity_id, metric, window=win
        )
        deltas: list[dict[str, Any]] = []
        prev: int | None = None
        for p in series:
            val = int(p.get("value") or 0)
            delta = val if prev is None else val - prev
            deltas.append({"at": p.get("at"), "value": val, "delta": delta})
            prev = val
        return {
            "metric": metric,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "window": win,
            "series": deltas,
        }

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
        stale = self._is_stale(latest) if latest else True
        available = latest is not None and not stale
        return {
            "project_id": project_id,
            "latest": latest,
            "series": series,
            "window": window,
            "stale": stale,
            # Explicit availability contract: an empty hot store must not
            # look identical to a broken one for the UI.
            "metrics_available": available,
            "metrics_unavailable_reason": (
                None if available else ("metrics not yet available" if latest is None else "metrics stale")
            ),
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
