"""Unit tests — metrics delta + pod ingest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.metrics.consumer.registry import handle_metrics_envelope
from prodavan.application.metrics.delta import should_emit_sample
from prodavan.core.events.envelope import metrics_envelope


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
    store.mark_event_processed = AsyncMock(return_value=True)
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
