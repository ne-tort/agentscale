"""Unit tests — project module toggle marks workspace outdated by default."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="emp-1", roles=frozenset({"employee"}))


@pytest.mark.asyncio
async def test_set_module_ids_marks_workspace_outdated_by_default() -> None:
    session = AsyncMock()
    row = MagicMock()
    row.id = "proj_1"
    row.cabinet_id = "cab_1"
    row.status = "active"

    with (
        patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=AsyncMock()),
        patch(
            "prodavan.application.modules.module_binding_service.ModuleBindingService"
        ) as binding_cls,
        patch(
            "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync",
            AsyncMock(
                return_value=__import__(
                    "prodavan.application.projects.workspace_sync_policy",
                    fromlist=["WorkspaceSyncNotification"],
                ).WorkspaceSyncNotification(
                    mode="deferred",
                    marked_outdated=1,
                    project_id="proj_1",
                    source="project_modules",
                )
            ),
        ) as defer,
    ):
        cmd = ProjectCommand(session)
        binding_cls.return_value.list_module_ids_for_cabinet = AsyncMock(
            return_value=["mod_files", "mod_mcp"]
        )
        session.execute = AsyncMock()
        cmd._access.require_access = AsyncMock(return_value=row)

        out = await cmd.set_module_ids(
            project_id="proj_1",
            module_ids=["mod_files"],
            principal=_principal(),
            employee=None,
        )

    assert out["module_ids"] == ["mod_files"]
    assert out["workspace_sync"]["marked_outdated"] == 1
    assert out["workspace_sync"]["mode"] == "deferred"
    defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_module_ids_triggers_rematerialize_when_auto_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "prodavan.config.settings.settings.projects_auto_rematerialize_on_cabinet_change",
        True,
    )
    session = AsyncMock()
    row = MagicMock()
    row.id = "proj_1"
    row.cabinet_id = "cab_1"
    row.status = "active"

    with (
        patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=AsyncMock()),
        patch(
            "prodavan.application.modules.module_binding_service.ModuleBindingService"
        ) as binding_cls,
        patch(
            "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync",
            AsyncMock(
                return_value=__import__(
                    "prodavan.application.projects.workspace_sync_policy",
                    fromlist=["WorkspaceSyncNotification"],
                ).WorkspaceSyncNotification(
                    mode="scheduled",
                    scheduled=1,
                    project_id="proj_1",
                    source="project_modules",
                    sync=("proj_1",),
                )
            ),
        ) as defer,
    ):
        cmd = ProjectCommand(session)
        binding_cls.return_value.list_module_ids_for_cabinet = AsyncMock(
            return_value=["mod_files", "mod_mcp"]
        )
        session.execute = AsyncMock()
        cmd._access.require_access = AsyncMock(return_value=row)

        out = await cmd.set_module_ids(
            project_id="proj_1",
            module_ids=["mod_files"],
            principal=_principal(),
            employee=None,
        )

    assert out["module_ids"] == ["mod_files"]
    assert out["workspace_sync"]["scheduled"] == 1
    defer.assert_awaited_once()
