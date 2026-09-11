"""Unit — mark_workspace_outdated_for_module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.projects.workspace_outdated import mark_workspace_outdated_for_module
from prodavan.domain.projects import ProjectStatus


@pytest.mark.asyncio
async def test_mark_workspace_outdated_for_module_marks_bound_projects() -> None:
    session = MagicMock()
    bind_result = MagicMock()
    bind_result.scalars.return_value.all.return_value = ["proj_a", "proj_b", "proj_a"]
    session.execute = AsyncMock(return_value=bind_result)

    proj_a = SimpleNamespace(status=ProjectStatus.ACTIVE, workspace_outdated_at=None)
    proj_b = SimpleNamespace(status=ProjectStatus.DELETED, workspace_outdated_at=None)

    async def _get(model, pid):  # noqa: ARG001
        return {"proj_a": proj_a, "proj_b": proj_b}.get(pid)

    session.get = AsyncMock(side_effect=_get)

    out = await mark_workspace_outdated_for_module(
        session, module_id="mod_prompts", source="module_meta"
    )
    assert out["marked_outdated"] == 1
    assert out["project_ids"] == ["proj_a"]
    assert proj_a.workspace_outdated_at is not None
    assert proj_b.workspace_outdated_at is None
