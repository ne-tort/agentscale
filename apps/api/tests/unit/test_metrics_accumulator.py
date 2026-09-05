"""Unit tests — Metrics BC accumulator, facts, overview merge."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.metrics.accumulator import MetricsAccumulator
from prodavan.application.metrics.command import MetricsCommand
from prodavan.application.metrics.consumer.registry import handle_metrics_envelope
from prodavan.application.metrics.overview_merge import overlay_store_counters
from prodavan.core.events.envelope import metrics_envelope, relation_event_envelope
from prodavan.domain.metrics.types import (
    ENTITY_CABINET,
    ENTITY_COMPANY,
    ENTITY_PROJECT,
    METRIC_AGENT_REQUESTS,
    METRIC_AGENT_TOKENS,
)
from prodavan.domain.relations import RELATION_GRANTED


@pytest.mark.asyncio
async def test_apply_counter_delta_cascades_to_cabinet_and_company() -> None:
    store = MagicMock()
    store.incr_counter = AsyncMock(side_effect=[1, 1, 1])
    store.append_counter_series = AsyncMock()
    store.get_parent = AsyncMock(return_value=None)
    acc = MetricsAccumulator(store)
    await acc.apply_counter_delta(
        metric=METRIC_AGENT_REQUESTS,
        entity_type=ENTITY_PROJECT,
        entity_id="prj_1",
        delta=1,
        company_id="co_1",
        cabinet_id="cab_1",
    )
    assert store.incr_counter.await_count == 3
    store.incr_counter.assert_any_await(ENTITY_PROJECT, "prj_1", METRIC_AGENT_REQUESTS, 1)
    store.incr_counter.assert_any_await(ENTITY_CABINET, "cab_1", METRIC_AGENT_REQUESTS, 1)
    store.incr_counter.assert_any_await(ENTITY_COMPANY, "co_1", METRIC_AGENT_REQUESTS, 1)


@pytest.mark.asyncio
async def test_usage_turn_tokens_only_without_request_only() -> None:
    store = MagicMock()
    store.incr_counter = AsyncMock(return_value=10)
    store.append_counter_series = AsyncMock()
    store.get_parent = AsyncMock(return_value=None)
    acc = MetricsAccumulator(store)
    await acc.apply_usage_turn(
        {
            "project_id": "prj_1",
            "cabinet_id": "cab_1",
            "company_id": "co_1",
            "input_tokens": 3,
            "output_tokens": 7,
        }
    )
    store.incr_counter.assert_any_await(ENTITY_PROJECT, "prj_1", METRIC_AGENT_TOKENS, 10)


@pytest.mark.asyncio
async def test_ingest_counter_delta_via_command() -> None:
    store = MagicMock()
    store.incr_counter = AsyncMock(return_value=1)
    store.append_counter_series = AsyncMock()
    store.get_parent = AsyncMock(return_value=None)
    cmd = MetricsCommand(accumulator=MetricsAccumulator(store), store=MagicMock())
    envelope = metrics_envelope(
        event_id="f1",
        event_type="metrics.counter.delta",
        company_id="co_1",
        project_id="prj_1",
        cabinet_id="cab_1",
        payload={
            "metric": METRIC_AGENT_REQUESTS,
            "entity_type": ENTITY_PROJECT,
            "entity_id": "prj_1",
            "delta": 1,
        },
    )
    await cmd.ingest_envelope(envelope)
    assert store.incr_counter.await_count >= 1


@pytest.mark.asyncio
async def test_overlay_prefers_store_agent_requests() -> None:
    acc = MagicMock()
    acc.get_overview_counters = AsyncMock(
        return_value={
            METRIC_AGENT_REQUESTS: 4,
            METRIC_AGENT_TOKENS: 100,
            "storage_bytes": 2048,
            "employees_total": None,
            "projects_total": None,
            "cabinets_total": None,
        }
    )
    out = await overlay_store_counters(
        {"agent_messages": 999, "agent_tokens_used": 1, "storage_bytes": 9},
        entity_type=ENTITY_CABINET,
        entity_id="cab_1",
        accumulator=acc,
    )
    assert out["agent_requests"] == 4
    assert out["agent_messages"] == 4
    assert out["agent_tokens_used"] == 100
    assert out["storage_bytes"] == 2048


@pytest.mark.asyncio
async def test_relation_granted_employee_company_updates_counter() -> None:
    store = MagicMock()
    store.put_link = AsyncMock()
    store.incr_counter = AsyncMock(return_value=1)
    store.append_counter_series = AsyncMock()
    store.get_parent = AsyncMock(return_value=None)
    envelope = relation_event_envelope(
        event_id="r1",
        event_type=RELATION_GRANTED,
        company_id="co_1",
        payload={
            "subject_kind": "employee",
            "subject_id": "emp_1",
            "object_kind": "company",
            "object_id": "co_1",
        },
    )
    with patch(
        "prodavan.application.metrics.consumer.relation_handler.build_counter_store",
        return_value=store,
    ):
        await handle_metrics_envelope(envelope)
    store.put_link.assert_awaited()
    store.incr_counter.assert_awaited()
