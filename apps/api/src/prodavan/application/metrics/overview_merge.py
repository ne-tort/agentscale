"""Merge Redis accumulated counters into overview DTO (prefer store over live SQL/FS)."""

from __future__ import annotations

from typing import Any

from prodavan.application.metrics.accumulator import MetricsAccumulator
from prodavan.domain.metrics.types import (
    METRIC_AGENT_REQUESTS,
    METRIC_AGENT_TOKENS,
    METRIC_CABINETS_TOTAL,
    METRIC_EMPLOYEES_TOTAL,
    METRIC_PROJECTS_TOTAL,
    METRIC_STORAGE_BYTES,
)


async def overlay_store_counters(
    metrics: dict[str, Any],
    *,
    entity_type: str,
    entity_id: str,
    accumulator: MetricsAccumulator | None = None,
) -> dict[str, Any]:
    """Prefer Metrics BC counters; keep SQL fallbacks only when store key is missing.

    Storage never falls back to live FS scan — missing store → 0.
    """
    acc = accumulator or MetricsAccumulator()
    counters = await acc.get_overview_counters(entity_type, entity_id)

    req = counters.get(METRIC_AGENT_REQUESTS)
    if req is not None:
        metrics["agent_requests"] = int(req)
        metrics["agent_messages"] = int(req)
    else:
        # Compat: SQL path may still set agent_messages (user_message count).
        fallback = int(metrics.get("agent_messages") or metrics.get("agent_requests") or 0)
        metrics["agent_requests"] = fallback
        metrics["agent_messages"] = fallback

    tokens = counters.get(METRIC_AGENT_TOKENS)
    if tokens is not None:
        metrics["agent_tokens_used"] = int(tokens)

    storage = counters.get(METRIC_STORAGE_BYTES)
    metrics["storage_bytes"] = int(storage) if storage is not None else 0

    emp = counters.get(METRIC_EMPLOYEES_TOTAL)
    if emp is not None:
        metrics["employees_total"] = int(emp)
        if "employees" in metrics:
            metrics["employees"] = int(emp)

    proj = counters.get(METRIC_PROJECTS_TOTAL)
    if proj is not None:
        metrics["projects_total"] = int(proj)

    cabs = counters.get(METRIC_CABINETS_TOTAL)
    if cabs is not None:
        metrics["cabinets_total"] = int(cabs)
        metrics["active_cabinets"] = int(cabs)
        metrics["cabinets_active"] = int(cabs)

    return metrics
