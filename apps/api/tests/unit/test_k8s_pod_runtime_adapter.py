"""K8sPodRuntimeAdapter unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.adapters.k8s.pod_runtime import K8sPodRuntimeAdapter
from prodavan.domain.pods import PodRuntimeContext
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


@pytest.mark.asyncio
async def test_ensure_running_idempotent_when_running() -> None:
    client = MagicMock()
    client.namespace = "prodavan-sandboxes"
    snap = PodSnapshot(
        name="pod-wk",
        uid="uid-1",
        phase="Running",
        restarts=0,
        ready=True,
        labels={"prodavan.io/hydrate-generation": "0"},
        hydrate_generation=0,
    )
    client.get_pod = AsyncMock(return_value=snap)
    client.wait_ready = AsyncMock(return_value=snap)
    client.create_pod = AsyncMock()

    adapter = K8sPodRuntimeAdapter(client=client)
    ctx = PodRuntimeContext(
        pod_id="pod_1",
        project_id="prj_1",
        company_id="cmp_1",
        workspace_key="wk",
    )
    await adapter.ensure_running(runtime_ref="pod-wk", context=ctx)

    client.create_pod.assert_not_awaited()
    client.wait_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_pause_ignores_not_found() -> None:
    from prodavan.infrastructure.k8s.errors import K8sNotFoundError

    client = MagicMock()
    client.delete_pod = AsyncMock(side_effect=K8sNotFoundError("missing"))
    adapter = K8sPodRuntimeAdapter(client=client)
    await adapter.pause(runtime_ref="pod-wk")


@pytest.mark.asyncio
async def test_ensure_running_retries_on_transient_not_found() -> None:
    from prodavan.infrastructure.k8s.errors import K8sNotFoundError

    client = MagicMock()
    client.namespace = "prodavan-sandboxes"
    client.get_pod = AsyncMock(return_value=None)
    client.create_pod = AsyncMock()
    client.wait_exists = AsyncMock(side_effect=K8sNotFoundError("missing"))
    client.wait_ready = AsyncMock()

    adapter = K8sPodRuntimeAdapter(client=client)
    ctx = PodRuntimeContext(
        pod_id="pod_1",
        project_id="prj_1",
        company_id="cmp_1",
        workspace_key="wk",
    )

    with pytest.raises(K8sNotFoundError):
        await adapter.ensure_running(runtime_ref="pod-wk", context=ctx)

    assert client.create_pod.await_count == 3
