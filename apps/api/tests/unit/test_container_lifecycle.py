"""Unit — project container lifecycle contract."""

from __future__ import annotations

import pytest

from prodavan.application.projects.container_lifecycle import (
    ensure_container_running,
    pause_container,
    reconcile_container,
)


@pytest.mark.asyncio
async def test_pause_container_desired_pod_absent() -> None:
    out = await pause_container(container_ref="object-ws:abc123")
    assert out["ok"] is True
    assert out["action"] == "desired_pod_absent"
    assert out["container_ref"] == "object-ws:abc123"
    assert out["pod_stop"] is True
    assert out["keep_volume"] is True
    assert out["pod_stopped"] is False  # stub until k8s wired


@pytest.mark.asyncio
async def test_ensure_and_reconcile() -> None:
    running = await ensure_container_running(container_ref="object-ws:x")
    assert running["pod_start"] is True
    stopped = await reconcile_container(container_ref="object-ws:x", desired_running=False)
    assert stopped["pod_stop"] is True
