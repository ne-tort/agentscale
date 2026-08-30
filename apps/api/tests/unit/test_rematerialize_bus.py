"""Unit tests — rematerialize Kafka bus cutover."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.core.jobs.rematerialize_bus import (
    REMATERIALIZE_REQUESTED,
    apply_rematerialize_requested_envelope,
    handle_rematerialize_requested_envelope,
    request_rematerialize_project,
)


@pytest.mark.asyncio
async def test_request_rematerialize_direct_enqueue_when_bus_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.jobs.rematerialize_bus.rematerialize_via_bus_enabled",
        lambda: False,
    )
    with patch(
        "prodavan.core.jobs.rematerialize_bus.enqueue_rematerialize_project",
        return_value={"enqueued": True, "project_id": "proj_1"},
    ) as enqueue:
        out = await request_rematerialize_project("proj_1")
    assert out["enqueued"] is True
    enqueue.assert_called_once_with("proj_1")


@pytest.mark.asyncio
async def test_request_rematerialize_publishes_when_bus_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.jobs.rematerialize_bus.rematerialize_via_bus_enabled",
        lambda: True,
    )
    with patch(
        "prodavan.core.jobs.rematerialize_bus.publish_rematerialize_requested",
        AsyncMock(return_value={"published": True, "event_id": "evt_1"}),
    ) as publish:
        out = await request_rematerialize_project(
            "proj_1",
            cabinet_id="cab_1",
            module_id="mod_files",
        )
    assert out["via_bus"] is True
    assert out["enqueued"] is True
    publish.assert_awaited_once()


def test_handle_rematerialize_requested_envelope() -> None:
    data = {
        "bus": "platform",
        "event_type": REMATERIALIZE_REQUESTED,
        "project_id": "proj_9",
    }
    assert handle_rematerialize_requested_envelope(data) == "proj_9"
    assert handle_rematerialize_requested_envelope({**data, "event_type": "project.created"}) is None


@pytest.mark.asyncio
async def test_apply_rematerialize_requested_envelope_enqueues() -> None:
    with patch(
        "prodavan.core.jobs.rematerialize_bus.enqueue_rematerialize_project",
        return_value={"enqueued": True, "project_id": "proj_2"},
    ) as enqueue:
        out = await apply_rematerialize_requested_envelope(
            {
                "bus": "platform",
                "event_type": REMATERIALIZE_REQUESTED,
                "project_id": "proj_2",
            }
        )
    assert out["ok"] is True
    enqueue.assert_called_once_with("proj_2")
