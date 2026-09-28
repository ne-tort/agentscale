"""Unit tests for SandboxPodMetricsAdapter (Wave 5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.adapters.agent_sandbox.metrics import (
    SandboxPodMetricsAdapter,
)

REF = "sandbox-claim-ws-demo-01"
SANDBOX_NAME = "prodavan-agent-pool-abcde"
NAMESPACE = "prodavan-sandboxes"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch):
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_sandbox_namespace", NAMESPACE)


def _k8s(metrics: dict[str, Any] | None = None) -> MagicMock:
    client = MagicMock()
    client.available = MagicMock(return_value=True)
    client.get_pod_metrics = AsyncMock(return_value=metrics)
    return client


def _runtime(status: dict[str, Any] | None = None) -> MagicMock:
    runtime = MagicMock()
    runtime.get_status = AsyncMock(return_value=status or {})
    return runtime


async def test_metrics_resolved_via_status_sandbox_name() -> None:
    k8s = _k8s({"cpu_millicores": 42, "memory_bytes": 1024})
    runtime = _runtime({"sandbox_name": SANDBOX_NAME})
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=runtime)

    out = await adapter.get_pod_metrics(runtime_ref=REF)

    assert out == {"cpu_millicores": 42, "memory_bytes": 1024}
    runtime.get_status.assert_awaited_once_with(runtime_ref=REF)
    k8s.get_pod_metrics.assert_awaited_once_with(SANDBOX_NAME)


async def test_metrics_preresolved_name_skips_status_fetch() -> None:
    k8s = _k8s({"cpu_millicores": 7, "memory_bytes": 512})
    runtime = _runtime({"sandbox_name": "should-not-be-used"})
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=runtime)

    out = await adapter.get_pod_metrics(runtime_ref=REF, sandbox_name=SANDBOX_NAME)

    assert out is not None
    runtime.get_status.assert_not_awaited()
    k8s.get_pod_metrics.assert_awaited_once_with(SANDBOX_NAME)


async def test_metrics_none_when_no_sandbox_bound_yet() -> None:
    k8s = _k8s({"cpu_millicores": 1, "memory_bytes": 1})
    runtime = _runtime({"sandbox_name": None})
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=runtime)

    assert await adapter.get_pod_metrics(runtime_ref=REF) is None
    k8s.get_pod_metrics.assert_not_awaited()


async def test_metrics_none_when_status_raises() -> None:
    k8s = _k8s({"cpu_millicores": 1, "memory_bytes": 1})
    runtime = MagicMock()
    runtime.get_status = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=runtime)

    assert await adapter.get_pod_metrics(runtime_ref=REF) is None
    k8s.get_pod_metrics.assert_not_awaited()


async def test_metrics_none_when_cluster_unreachable() -> None:
    k8s = _k8s()
    k8s.get_pod_metrics = AsyncMock(side_effect=ConnectionError("apiserver down"))
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=_runtime())

    assert (
        await adapter.get_pod_metrics(runtime_ref=REF, sandbox_name=SANDBOX_NAME) is None
    )


async def test_metrics_none_when_k8s_client_unavailable() -> None:
    k8s = _k8s()
    k8s.available = MagicMock(return_value=False)
    adapter = SandboxPodMetricsAdapter(client=k8s, runtime=_runtime())

    assert (
        await adapter.get_pod_metrics(runtime_ref=REF, sandbox_name=SANDBOX_NAME) is None
    )
    k8s.get_pod_metrics.assert_not_awaited()
