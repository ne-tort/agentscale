"""Accumulate metric facts with cascade via entity link graph."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.application.metrics.adapters.redis_counter_store import (
    RedisCounterStore,
    build_counter_store,
)
from prodavan.domain.metrics.types import (
    ENTITY_CABINET,
    ENTITY_COMPANY,
    ENTITY_EMPLOYEE,
    ENTITY_PROJECT,
    ENTITY_SESSION,
    METRIC_AGENT_REQUESTS,
    METRIC_AGENT_TOKENS,
    METRIC_STORAGE_BYTES,
)

logger = logging.getLogger(__name__)


class MetricsAccumulator:
    def __init__(self, store: RedisCounterStore | None = None) -> None:
        self._store = store or build_counter_store()

    async def _side_incr(
        self,
        *,
        metric: str,
        delta: int,
        session_id: str | None,
        employee_id: str | None,
        at: str | None,
    ) -> None:
        """Incr session/employee without cascading (avoids double-counting company)."""
        if session_id:
            val = await self._store.incr_counter(ENTITY_SESSION, session_id, metric, delta)
            await self._store.append_counter_series(
                ENTITY_SESSION, session_id, metric, value=val, at=at
            )
        if employee_id:
            val = await self._store.incr_counter(ENTITY_EMPLOYEE, employee_id, metric, delta)
            await self._store.append_counter_series(
                ENTITY_EMPLOYEE, employee_id, metric, value=val, at=at
            )

    async def apply_counter_delta(
        self,
        *,
        metric: str,
        entity_type: str,
        entity_id: str,
        delta: int,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        employee_id: str | None = None,
        at: str | None = None,
    ) -> None:
        _ = project_id
        if not entity_id or not metric or delta == 0:
            return
        val = await self._store.incr_counter(entity_type, entity_id, metric, delta)
        await self._store.append_counter_series(
            entity_type, entity_id, metric, value=val, at=at
        )
        # Cascade using explicit ids from event, then link graph parents.
        parents: list[tuple[str, str]] = []
        if entity_type == ENTITY_PROJECT:
            cab = cabinet_id or await self._store.get_parent(
                ENTITY_PROJECT, entity_id, ENTITY_CABINET
            )
            if cab:
                parents.append((ENTITY_CABINET, cab))
            co = company_id or (
                await self._store.get_parent(ENTITY_CABINET, cab, ENTITY_COMPANY) if cab else None
            )
            if co:
                parents.append((ENTITY_COMPANY, co))
        elif entity_type == ENTITY_CABINET:
            co = company_id or await self._store.get_parent(
                ENTITY_CABINET, entity_id, ENTITY_COMPANY
            )
            if co:
                parents.append((ENTITY_COMPANY, co))

        for ptype, pid in parents:
            pval = await self._store.incr_counter(ptype, pid, metric, delta)
            await self._store.append_counter_series(ptype, pid, metric, value=pval, at=at)

        await self._side_incr(
            metric=metric,
            delta=delta,
            session_id=session_id,
            employee_id=employee_id,
            at=at,
        )

    async def apply_usage_turn(self, payload: dict[str, Any], *, at: str | None = None) -> None:
        project_id = str(payload.get("project_id") or "").strip()
        cabinet_id = str(payload.get("cabinet_id") or "").strip() or None
        company_id = str(payload.get("company_id") or "").strip() or None
        session_id = str(payload.get("session_id") or "").strip() or None
        employee_id = str(payload.get("employee_id") or "").strip() or None
        if not project_id:
            return
        if payload.get("request_only"):
            await self.apply_counter_delta(
                metric=METRIC_AGENT_REQUESTS,
                entity_type=ENTITY_PROJECT,
                entity_id=project_id,
                delta=1,
                company_id=company_id,
                cabinet_id=cabinet_id,
                session_id=session_id,
                employee_id=employee_id,
                at=at,
            )
            return
        try:
            tokens = int(payload.get("input_tokens") or 0) + int(payload.get("output_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens > 0:
            await self.apply_counter_delta(
                metric=METRIC_AGENT_TOKENS,
                entity_type=ENTITY_PROJECT,
                entity_id=project_id,
                delta=tokens,
                company_id=company_id,
                cabinet_id=cabinet_id,
                session_id=session_id,
                employee_id=employee_id,
                at=at,
            )

    async def apply_storage_snapshot(
        self,
        *,
        entity_type: str,
        entity_id: str,
        bytes_value: int,
        company_id: str | None = None,
        cabinet_id: str | None = None,
        at: str | None = None,
    ) -> None:
        """Absolute size for one entity. Parents are set by sampler/backfill rollups (no cascade)."""
        _ = company_id, cabinet_id
        await self._store.put_storage_snapshot(entity_type, entity_id, bytes_value)
        await self._store.append_counter_series(
            entity_type, entity_id, METRIC_STORAGE_BYTES, value=int(bytes_value), at=at
        )

    async def get_overview_counters(
        self, entity_type: str, entity_id: str
    ) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for metric in (
            METRIC_AGENT_REQUESTS,
            METRIC_AGENT_TOKENS,
            METRIC_STORAGE_BYTES,
            "employees_total",
            "projects_total",
            "cabinets_total",
        ):
            out[metric] = await self._store.get_counter(entity_type, entity_id, metric)
        return out
