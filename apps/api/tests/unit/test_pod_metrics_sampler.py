"""Unit tests — PodMetricsSampler hot store + heartbeat."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.metrics_sampler import PodMetricsSampler
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


def _project() -> ProjectRow:
    return ProjectRow(
        id="prj_test1234567890",
        company_id="cmp_test1234567890",
        cabinet_id="cab_test1234567890",
        owner_employee_id="emp_test1234567890",
        name="Demo",
        slug="demo",
        status=ProjectStatus.ACTIVE,
        visibility_mode="cabinet_shared",
        workspace_key="wk_demo",
        container_ref="object-ws:wk_demo",
    )


def _pod() -> ProjectPodRow:
    return ProjectPodRow(
        id="pod_live",
        project_id="prj_test1234567890",
        workspace_key="wk_demo",
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING.value,
        runtime_ref="pod-wk-demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_sampler_ingests_hot_store_when_kafka_enabled() -> None:
    session = AsyncMock()
    store = MagicMock()
    store.get_pod_last_sample = AsyncMock(return_value=None)
    store.put_pod_last_sample = AsyncMock()
    metrics_port = AsyncMock()
    metrics_port.get_pod_metrics = AsyncMock(
        return_value={"cpu_millicores": 100, "memory_bytes": 4096}
    )
    runtime_port = AsyncMock()
    runtime_port.get_status = AsyncMock(return_value={"phase": "Running", "ready": True})

    svc = PodMetricsSampler(session)
    svc._store = store
    ingest = AsyncMock()
    svc._ingest_hot = ingest  # type: ignore[method-assign]

    with patch("prodavan.application.pod_service.metrics_sampler.settings") as mock_settings:
        mock_settings.kafka_enabled = True
        mock_settings.pod_runtime_mode = "k8s"
        result = await svc._sample_one(
            pod=_pod(),
            project=_project(),
            metrics_port=metrics_port,
            runtime_port=runtime_port,
        )

    assert result == "sampled"
    ingest.assert_awaited_once()
    store.put_pod_last_sample.assert_awaited_once()


@pytest.mark.asyncio
async def test_sampler_heartbeats_when_sample_unchanged() -> None:
    session = AsyncMock()
    store = MagicMock()
    now = datetime.now(UTC)
    last = {
        "cpu_millicores": 100,
        "memory_bytes": 4096,
        "phase": "Running",
        "restarts": 0,
        "ready": True,
        "timestamp": (now - timedelta(seconds=5)).isoformat(),
    }
    store.get_pod_last_sample = AsyncMock(return_value=last)
    store.get_project_latest = AsyncMock(return_value=dict(last, pod_id="pod_live"))
    store.put_project_latest = AsyncMock()
    store.put_pod_last_sample = AsyncMock()
    metrics_port = AsyncMock()
    metrics_port.get_pod_metrics = AsyncMock(
        return_value={"cpu_millicores": 100, "memory_bytes": 4096}
    )
    runtime_port = AsyncMock()
    runtime_port.get_status = AsyncMock(return_value={"phase": "Running", "ready": True, "restarts": 0})

    svc = PodMetricsSampler(session)
    svc._store = store

    with patch("prodavan.application.pod_service.metrics_sampler.settings") as mock_settings:
        mock_settings.metrics_sample_interval_sec = 15
        mock_settings.pod_runtime_mode = "k8s"
        result = await svc._sample_one(
            pod=_pod(),
            project=_project(),
            metrics_port=metrics_port,
            runtime_port=runtime_port,
        )

    assert result == "skipped"
    store.put_project_latest.assert_awaited_once()
    store.put_pod_last_sample.assert_awaited_once()


@pytest.mark.asyncio
async def test_sampler_skips_when_metrics_unavailable() -> None:
    session = AsyncMock()
    store = MagicMock()
    metrics_port = AsyncMock()
    metrics_port.get_pod_metrics = AsyncMock(return_value=None)
    runtime_port = AsyncMock()
    runtime_port.get_status = AsyncMock(return_value={"phase": "Running", "ready": True})

    svc = PodMetricsSampler(session)
    svc._store = store
    ingest = AsyncMock()
    svc._ingest_hot = ingest  # type: ignore[method-assign]

    with patch("prodavan.application.pod_service.metrics_sampler.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        result = await svc._sample_one(
            pod=_pod(),
            project=_project(),
            metrics_port=metrics_port,
            runtime_port=runtime_port,
        )

    assert result == "skipped"
    ingest.assert_not_awaited()
