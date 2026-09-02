"""Unit tests — cabinet rematerialize scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prodavan.application.projects.rematerialize_scheduler import (
    module_has_materialize_rules,
    schedule_cabinet_binding_change_rematerialize,
    schedule_cabinet_rematerialize,
)
from prodavan.application.projects.workspace_sync_policy import WorkspaceSyncNotification


@pytest.mark.asyncio
async def test_module_has_materialize_rules_product_modules() -> None:
    session = AsyncMock()
    assert await module_has_materialize_rules(session, module_id="mod_files") is True
    assert await module_has_materialize_rules(session, module_id="mod_mcp") is True


@pytest.mark.asyncio
async def test_schedule_cabinet_rematerialize_enqueues_projects() -> None:
    session = AsyncMock()
    notification = WorkspaceSyncNotification(
        mode="scheduled",
        scheduled=2,
        cabinet_id="cab_1",
        module_id="mod_files",
        source="cabinet_module",
        enqueued=("proj_a", "proj_b"),
    )
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
        AsyncMock(return_value=notification),
    ) as defer:
        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_files"
        )

    assert out["scheduled"] == 2
    defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_skips_modules_without_materialize() -> None:
    session = AsyncMock()
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.module_has_materialize_rules",
        AsyncMock(return_value=False),
    ):
        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_other"
        )
    assert out["skipped"] is True
    assert out["scheduled"] == 0


@pytest.mark.asyncio
async def test_schedule_cabinet_rematerialize_marks_outdated_when_auto_off() -> None:
    session = AsyncMock()
    notification = WorkspaceSyncNotification(
        mode="deferred",
        marked_outdated=2,
        cabinet_id="cab_1",
        module_id="mod_files",
        source="cabinet_module",
    )
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
        AsyncMock(return_value=notification),
    ) as defer:
        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_files"
        )
    assert out["marked_outdated"] == 2
    assert out["mode"] == "deferred"
    defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_binding_change_rematerializes_all_projects() -> None:
    session = AsyncMock()
    notification = WorkspaceSyncNotification(
        mode="scheduled",
        scheduled=1,
        cabinet_id="cab_1",
        source="cabinet_binding",
        enqueued=("proj_a",),
    )
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
        AsyncMock(return_value=notification),
    ) as defer:
        out = await schedule_cabinet_binding_change_rematerialize(
            session, cabinet_id="cab_1"
        )

    assert out["scheduled"] == 1
    defer.assert_awaited_once_with(
        session,
        cabinet_id="cab_1",
        module_id=None,
        source="cabinet_binding",
    )


@pytest.mark.asyncio
async def test_schedule_binding_change_via_bus_when_enabled() -> None:
    session = AsyncMock()
    notification = WorkspaceSyncNotification(
        mode="scheduled",
        scheduled=1,
        cabinet_id="cab_1",
        source="cabinet_binding",
        enqueued=("proj_a",),
    )
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
        AsyncMock(return_value=notification),
    ):
        out = await schedule_cabinet_binding_change_rematerialize(
            session, cabinet_id="cab_1"
        )

    assert out["scheduled"] == 1
    assert out["enqueued"] == ["proj_a"]
