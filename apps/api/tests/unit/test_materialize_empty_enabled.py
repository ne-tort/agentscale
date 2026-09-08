"""Regression: empty MP list must not materialize all cabinet modules."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.projects.materialize import ProjectMaterializeService


@pytest.mark.asyncio
async def test_sync_project_empty_enabled_does_not_fallback_to_cabinet_modules() -> None:
    svc = ProjectMaterializeService()
    session = AsyncMock()
    project = SimpleNamespace(
        id="prj_1",
        cabinet_id="cab_1",
        name="P",
        workspace_key="ws_1",
        materialize_manifest={"mod_a": ["a.md"], "mod_b": ["b.md"]},
    )
    result = SimpleNamespace(module_paths={})

    with (
        patch.object(svc, "_run_materialize", AsyncMock(return_value=result)) as run,
        patch("prodavan.application.projects.materialize.MaterializePlanner") as planner_cls,
        patch("prodavan.application.projects.materialize.WorkspaceLayoutWriter") as writer_cls,
    ):
        planner = planner_cls.return_value
        planner.load_workspace_roots = AsyncMock(return_value=[])
        writer = writer_cls.return_value
        writer.wipe_prefix = MagicMock()
        writer.remove_relative_path = MagicMock()

        await svc.sync_project(
            session=session,
            project=project,
            enabled_module_ids=[],
            all_cabinet_module_ids=["mod_a", "mod_b"],
        )

    run.assert_awaited_once()
    assert run.await_args.kwargs["enabled_module_ids"] == []
