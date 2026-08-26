"""Unit — project container lifecycle contract."""

from __future__ import annotations

import pytest

from prodavan.application.projects.container_lifecycle import pause_container


@pytest.mark.asyncio
async def test_pause_container_keeps_volume() -> None:
    out = await pause_container(container_ref="object-ws:abc123")
    assert out["ok"] is True
    assert out["action"] == "keep_volume"
    assert out["container_ref"] == "object-ws:abc123"
    assert out["pod_stop"] is False
