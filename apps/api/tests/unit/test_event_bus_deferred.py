"""Unit tests — deferred C-EVENT-BUS publish after commit."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from prodavan.core.events.deferred import (
    _on_after_commit,
    _on_after_rollback,
    schedule_envelope_publish,
    schedule_project_trigger_publish,
)
from prodavan.core.events.envelope import platform_envelope


def _mock_async_session() -> MagicMock:
    sync = MagicMock()
    sync.info = {}
    session = MagicMock()
    session.sync_session = sync
    return session


@pytest.mark.asyncio
async def test_deferred_publish_runs_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list = []

    async def _capture(items: list) -> None:
        published.extend(items)

    monkeypatch.setattr("prodavan.core.events.deferred._publish_pending", _capture)

    session = _mock_async_session()
    schedule_envelope_publish(session, platform_envelope(event_id="evt_1", event_type="project.created"))
    _on_after_commit(session.sync_session)
    await asyncio.sleep(0.05)
    assert len(published) == 1
    assert published[0].event_id == "evt_1"


def test_deferred_publish_cleared_on_rollback() -> None:
    session = _mock_async_session()
    schedule_project_trigger_publish(
        session,
        event_id="trg_1",
        kind="chat.message",
        project_id="proj_1",
    )
    assert len(session.sync_session.info["_prodavan_event_bus_pending"]) == 1
    _on_after_rollback(session.sync_session)
    assert "_prodavan_event_bus_pending" not in session.sync_session.info
