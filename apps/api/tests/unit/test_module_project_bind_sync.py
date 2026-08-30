"""Unit tests — admin module↔project bind triggers rematerialize."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.modules.module_service import ModuleService


@pytest.mark.asyncio
async def test_bind_project_triggers_rematerialize_background() -> None:
    session = AsyncMock()
    svc = ModuleService(session)
    mod = MagicMock()
    mod.id = "mod_custom"

    with (
        patch("prodavan.application.pod_service.command.build_pod_runtime", return_value=AsyncMock()),
        patch.object(svc, "_get_row", AsyncMock(return_value=mod)),
        patch.object(svc._bindings, "bind_project", AsyncMock()) as bind,
        patch(
            "prodavan.application.project_service.command.ProjectCommand.rematerialize_background",
            AsyncMock(return_value={"ok": True, "hydrate_generation": 1}),
        ) as remat,
    ):
        out = await svc.bind_project(module_id="mod_custom", project_id="proj_1")

    bind.assert_awaited_once_with("mod_custom", "proj_1")
    session.commit.assert_awaited()
    remat.assert_awaited_once_with(project_id="proj_1")
    assert out["sync"]["ok"] is True
