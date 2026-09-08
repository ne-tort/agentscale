"""Unit tests — admin module↔project bind triggers workspace sync policy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.module_service import ModuleService
from prodavan.application.projects.workspace_sync_policy import WorkspaceSyncNotification


@pytest.mark.asyncio
async def test_bind_project_triggers_workspace_sync_policy() -> None:
    session = AsyncMock()
    svc = ModuleService(session)
    mod = MagicMock()
    mod.id = "mod_custom"

    notification = WorkspaceSyncNotification(
        mode="deferred",
        marked_outdated=1,
        project_id="proj_1",
        source="module_bind",
    )

    with (
        patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=AsyncMock()),
        patch.object(svc, "_get_row", AsyncMock(return_value=mod)),
        patch.object(
            svc._bindings,
            "bind_project",
            AsyncMock(
                return_value={
                    "module_id": "mod_custom",
                    "project_id": "proj_1",
                    "bind_kind": "local",
                    "child_may_edit": True,
                }
            ),
        ) as bind,
        patch(
            "prodavan.application.projects.workspace_sync_policy.defer_or_schedule_project_sync",
            AsyncMock(return_value=notification),
        ) as defer,
    ):
        out = await svc.bind_project(module_id="mod_custom", project_id="proj_1")

    bind.assert_awaited_once_with(
        "mod_custom",
        "proj_1",
        bind_kind="local",
        child_may_edit=None,
    )
    assert session.commit.await_count >= 2
    defer.assert_awaited_once()
    assert out["workspace_sync"]["marked_outdated"] == 1
    assert out["workspace_sync"]["mode"] == "deferred"
