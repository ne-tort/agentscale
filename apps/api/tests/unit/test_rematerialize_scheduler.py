"""Unit tests — cabinet rematerialize scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.projects.rematerialize_scheduler import (
    module_has_materialize_rules,
    schedule_cabinet_binding_change_rematerialize,
    schedule_cabinet_rematerialize,
)
from prodavan.application.projects.workspace_sync_policy import WorkspaceSyncNotification


@pytest.mark.asyncio
async def test_module_has_materialize_rules_reads_meta_document() -> None:
    session = AsyncMock()
    with patch(
        "prodavan.application.projects.rematerialize_scheduler.ModuleMetaDocumentService"
    ) as meta_cls:
        meta_cls.return_value.get_document = AsyncMock(
            return_value={"body": [{"id": "r1", "enabled": True}]}
        )
        assert await module_has_materialize_rules(session, module_id="mod_files") is True
        meta_cls.return_value.get_document = AsyncMock(return_value={"body": []})
        assert await module_has_materialize_rules(session, module_id="mod_mcp") is False


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
    bindings = MagicMock()
    bindings.list_project_ids = AsyncMock(return_value=["proj_a", "proj_b"])
    with (
        patch(
            "prodavan.application.projects.rematerialize_scheduler.module_has_materialize_rules",
            AsyncMock(return_value=True),
        ),
        patch(
            "prodavan.application.modules.module_binding_service.ModuleBindingService",
            return_value=bindings,
        ),
        patch(
            "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
            AsyncMock(return_value=notification),
        ) as defer,
    ):
        # cabinet project query returns both bound projects
        result = MagicMock()
        result.all.return_value = [("proj_a",), ("proj_b",)]
        session.execute = AsyncMock(return_value=result)
        out = await schedule_cabinet_rematerialize(
            session, cabinet_id="cab_1", module_id="mod_files"
        )

    assert out["scheduled"] == 2
    defer.assert_awaited_once()
    assert set(defer.await_args.kwargs["project_ids"]) == {"proj_a", "proj_b"}


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
    bindings = MagicMock()
    bindings.list_project_ids = AsyncMock(return_value=["proj_a", "proj_b"])
    result = MagicMock()
    result.all.return_value = [("proj_a",), ("proj_b",)]
    session.execute = AsyncMock(return_value=result)
    with (
        patch(
            "prodavan.application.projects.rematerialize_scheduler.module_has_materialize_rules",
            AsyncMock(return_value=True),
        ),
        patch(
            "prodavan.application.modules.module_binding_service.ModuleBindingService",
            return_value=bindings,
        ),
        patch(
            "prodavan.application.projects.rematerialize_scheduler.defer_or_schedule_cabinet_sync",
            AsyncMock(return_value=notification),
        ) as defer,
    ):
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
