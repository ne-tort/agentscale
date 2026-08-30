"""Unit tests — project module toggle triggers workspace sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.project_service.command import ProjectCommand
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="emp-1", roles=frozenset({"employee"}))


@pytest.mark.asyncio
async def test_set_module_ids_triggers_rematerialize_background() -> None:
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
        patch.object(
            ProjectCommand,
            "rematerialize_background",
            AsyncMock(return_value={"ok": True, "hydrate_generation": 1}),
        ) as remat,
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
    remat.assert_awaited_once_with(project_id="proj_1")
