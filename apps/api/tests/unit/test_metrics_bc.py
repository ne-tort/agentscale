"""Unit tests — metrics delta + pod ingest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from prodavan.application.metrics.consumer.registry import handle_metrics_envelope
from prodavan.application.metrics.delta import should_emit_sample
from prodavan.core.events.envelope import metrics_envelope, platform_envelope
from prodavan.domain.metrics.types import (
    ENTITY_CABINET,
    ENTITY_COMPANY,
    ENTITY_PROJECT,
    METRIC_PROJECTS_TOTAL,
)


def test_should_emit_sample_on_first_reading() -> None:
    assert should_emit_sample(last=None, current={"cpu_millicores": 10, "memory_bytes": 1000}) is True


def test_should_emit_sample_on_cpu_delta() -> None:
    now = datetime.now(UTC)
    last = {
        "cpu_millicores": 100,
        "memory_bytes": 1000,
        "timestamp": (now - timedelta(seconds=10)).isoformat(),
    }
    current = {"cpu_millicores": 200, "memory_bytes": 1000, "timestamp": now.isoformat()}
    assert should_emit_sample(last=last, current=current, now=now) is True


def test_should_skip_sample_when_unchanged() -> None:
    now = datetime.now(UTC)
    sample = {
        "cpu_millicores": 100,
        "memory_bytes": 1000,
        "phase": "Running",
        "restarts": 0,
        "ready": True,
        "timestamp": (now - timedelta(seconds=5)).isoformat(),
    }
    current = dict(sample, timestamp=now.isoformat())
    assert should_emit_sample(last=sample, current=current, now=now) is False


@pytest.mark.asyncio
async def test_handle_metrics_envelope_ingests_pod_sample() -> None:
    store = MagicMock()
    store.is_event_complete = AsyncMock(return_value=False)
    store.claim_event = AsyncMock(return_value=True)
    store.complete_event = AsyncMock()
    store.release_event_claim = AsyncMock()
    store.put_project_latest = AsyncMock()
    store.append_project_series = AsyncMock()
    store.put_pod_last_sample = AsyncMock()
    envelope = metrics_envelope(
        event_id="met_test1",
        event_type="pod.metrics.sample",
        company_id="co_1",
        project_id="prj_1",
        cabinet_id="cab_1",
        payload={"pod_id": "pod_1", "cpu_millicores": 120, "memory_bytes": 4096},
    )
    session = AsyncMock()
    with patch("prodavan.application.metrics.consumer.pod_metrics_handler.get_session_factory") as factory:
        factory.return_value = MagicMock(
            __aenter__=AsyncMock(return_value=session),
            __aexit__=AsyncMock(return_value=False),
        )
        with patch("prodavan.application.metrics.consumer.pod_metrics_handler.build_metrics_store", return_value=store):
            await handle_metrics_envelope(envelope)
    store.put_project_latest.assert_awaited_once()
    store.append_project_series.assert_awaited_once()
    store.complete_event.assert_awaited_once_with("met_test1")


@pytest.mark.asyncio
async def test_pod_metrics_claim_is_released_when_ingest_fails() -> None:
    """A failed side effect must leave the Kafka event eligible for redelivery."""
    from prodavan.application.metrics.consumer.pod_metrics_handler import handle_pod_metrics_envelope

    store = MagicMock()
    store.is_event_complete = AsyncMock(return_value=False)
    store.claim_event = AsyncMock(return_value=True)
    store.complete_event = AsyncMock()
    store.release_event_claim = AsyncMock()
    session = AsyncMock()
    envelope = metrics_envelope(
        event_id="met_retry",
        event_type="pod.metrics.sample",
        project_id="prj_1",
        payload={"pod_id": "pod_1"},
    )
    with patch(
        "prodavan.application.metrics.consumer.pod_metrics_handler.get_session_factory"
    ) as factory, patch(
        "prodavan.application.metrics.consumer.pod_metrics_handler.build_metrics_store",
        return_value=store,
    ), patch(
        "prodavan.application.metrics.consumer.pod_metrics_handler.MetricsCommand.ingest_envelope",
        new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
    ):
        factory.return_value = MagicMock(
            __aenter__=AsyncMock(return_value=session),
            __aexit__=AsyncMock(return_value=False),
        )
        with pytest.raises(RuntimeError, match="redis unavailable"):
            await handle_pod_metrics_envelope(envelope)

    store.claim_event.assert_awaited_once_with("met_retry")
    store.release_event_claim.assert_awaited_once_with("met_retry")


@pytest.mark.asyncio
async def test_project_created_redelivery_skips_duplicate_seed() -> None:
    counter_store = MagicMock()
    counter_store.put_link = AsyncMock()
    counter_store.incr_counter = AsyncMock(return_value=1)
    counter_store.append_counter_series = AsyncMock()
    dedup = MagicMock()
    dedup.is_event_complete = AsyncMock(side_effect=[False, True])
    dedup.claim_event = AsyncMock(return_value=True)
    dedup.complete_event = AsyncMock()
    dedup.release_event_claim = AsyncMock()
    envelope = platform_envelope(
        event_id="project_created_1",
        event_type="project.created",
        company_id="co_1",
        project_id="prj_1",
        cabinet_id="cab_1",
    )
    with patch(
        "prodavan.application.metrics.consumer.registry.build_counter_store",
        return_value=counter_store,
    ), patch(
        "prodavan.application.metrics.consumer.registry.build_metrics_store",
        return_value=dedup,
    ):
        await handle_metrics_envelope(envelope)
        await handle_metrics_envelope(envelope)
    # Seeding a project deliberately cascades: the project's cabinet aggregate and
    # its parent company aggregate each move by exactly one. The previous
    # expectation of a single incr_counter encoded a one-level seed and missed the
    # company rollup, so a redelivery that double-counted either level went
    # undetected.
    assert counter_store.put_link.await_args_list == [
        call(
            parent_type=ENTITY_CABINET,
            parent_id="cab_1",
            child_type=ENTITY_PROJECT,
            child_id="prj_1",
        ),
        call(
            parent_type=ENTITY_COMPANY,
            parent_id="co_1",
            child_type=ENTITY_CABINET,
            child_id="cab_1",
        ),
    ]
    assert counter_store.incr_counter.await_args_list == [
        call(ENTITY_CABINET, "cab_1", METRIC_PROJECTS_TOTAL, 1),
        call(ENTITY_COMPANY, "co_1", METRIC_PROJECTS_TOTAL, 1),
    ]
    dedup.claim_event.assert_awaited_once_with("platform_facts:project_created_1")
    dedup.complete_event.assert_awaited_once_with("platform_facts:project_created_1")
